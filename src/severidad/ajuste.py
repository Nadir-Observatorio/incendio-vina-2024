"""Local adjustment of the burned / unburned threshold: an additional result of the study.

The published headline (rule fixed before any result was seen) is not replaced. This module fits,
for dNBR, RdNBR and RBR, the threshold that best separates burned from unburned against the
EMSR715 delineation (WorldView-2, 2 m), and validates it on spatial blocks that took no part in
the fit. The delineation has a single category ("Burnt area"), so only the burned / unburned cut
can be fitted: the limits between severity classes stay those of Key and Benson (2006).

Protocol, fixed on ``ADJUSTMENT_DATE`` before computing (parameters below and in ``estudio``):

- Domain and reference: the domain of measurement 2 (municipalities, WorldView-2 footprint, valid
  pixel) and the EMSR715 AOI01 delineation v2 rasterised with the pixel-centre rule. Every pixel
  is used (census): the reference is wall to wall, a sample would only add noise.
- The 1,200 pixels sampled by measurement 2 (300 per stratum for A and for B, rebuilt with the
  study seed) take part in neither the fit nor the validation.
- Blocks of ``BLOCK_PX`` pixels (2 km) aligned to the class raster. Blocks with domain pixels are
  sorted by reference burned fraction and taken in consecutive pairs; one block of each pair goes
  to the fit and the other to the validation, at random with the study seed.
- Validation pixels closer than ``estudio.M2_MIN_DISTANCE_PX`` (Chebyshev, 200 m) to a fit block
  are left out (guard band).
- Indices (x 1000): dNBR as published (offset of the unburned ring removed); RdNBR = dNBR /
  sqrt(|NBRpre|), |NBRpre| raised to 0.001 when smaller (Miller and Thode 2007); RBR = dNBR /
  (NBRpre + 1.001) (Parks et al. 2014). The offset is removed before relativising (Parks et al.
  2018). A pixel is burned when its index is at least the threshold.
- Threshold: the one with the highest accuracy in area over the fit pixels (the quantity that the
  weighted accuracy of measurement 2 estimates); ties go to the lowest threshold. Youden's index
  and the area balance are reported as sensitivity only.
- Uncertainty: block bootstrap (``BOOTSTRAP_REPLICATES`` replicates) of the fit blocks for the
  threshold and the hectares; of the validation blocks for the validation figures.
- Validation: census of the validation pixels for the three fitted thresholds and for the
  published thresholds A and B, on the same pixels.
- Index of the additional figure: the one with the highest validation accuracy; if it beats
  dNBR by less than ``estudio.HEADLINE_TIE_POINTS``, dNBR.
- EMSN188 (independent check, never used to fit): up to 300 damage points in validation blocks,
  not in the measurement 1 sample, minimum distance 100 m; share in burned with a Wilson interval.
  Amendment of 2026-11-11, before any figure was seen: the first run found that only 145 points
  fit in the validation blocks with 100 m between them, so the check takes every point that fits
  (up to 300) instead of stopping; no other rule changes.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import numpy as np
import rasterio
from affine import Affine
from rasterio.features import rasterize
from scipy.ndimage import maximum_filter
from shapely.ops import unary_union

from . import calculo, estudio, validacion
from .errors import DataError

logger = logging.getLogger(__name__)

ADJUSTMENT_DATE: Final = "2026-11-11"
ADJUSTMENT_FILE: Final = estudio.ADJUSTMENT_FILE
BLOCK_PX: Final = 100                     # 2 km at 20 m
BOOTSTRAP_REPLICATES: Final = 1000
RDNBR_MIN_PRE: Final = 0.001              # Miller and Thode (2007)
RBR_ADD: Final = 1.001                    # Parks et al. (2014)
GRID: Final = (-2000, 4000)               # thresholds searched, index units x 1000
INDICES: Final = ("dNBR", "RdNBR", "RBR")
EMSN188_SAMPLE: Final = 300
# Seeds of the four random steps, derived from the study seed.
STREAMS: Final = MappingProxyType({"blocks": 1, "fit_bootstrap": 2, "validation_bootstrap": 3})

NOTE: Final = (
    "Additional result, not the headline: local fit of the burned / unburned threshold against "
    "EMSR715 with spatial blocks. The headline in metricas.json (rule fixed before any result was "
    "seen) is unchanged. Only the burned / unburned cut is fitted; the severity class limits are "
    "Key and Benson (2006). Validated on blocks of this fire only: not tested on other fires.")
PROTOCOL: Final[Mapping[str, str]] = MappingProxyType({
    "reference": (
        "EMSR715 AOI01 delineation v2 in the measurement 2 domain (municipalities, WorldView-2 "
        "footprint, valid pixel), pixel-centre rule, every pixel (census)"),
    "excluded": "the 1,200 pixels sampled by measurement 2 (rebuilt with the study seed)",
    "blocks": (
        "2 km blocks aligned to clases.tif, sorted by reference burned fraction and split in "
        "pairs at random (study seed): one to the fit, one to the validation; validation pixels "
        "closer than 10 px (200 m, Chebyshev) to a fit block are left out"),
    "indices": (
        "x 1000; dNBR as published (ring offset removed); RdNBR = dNBR / sqrt(max(|NBRpre|, "
        "0.001)) (Miller and Thode 2007); RBR = dNBR / (NBRpre + 1.001) (Parks et al. 2014); "
        "offset removed before relativising (Parks et al. 2018); burned when index >= threshold"),
    "criterion": (
        "highest accuracy in area over the fit pixels, ties to the lowest threshold; Youden's "
        "index and area balance reported as sensitivity"),
    "uncertainty": (
        "block bootstrap, 1000 replicates: fit blocks for threshold and hectares, validation "
        "blocks for validation figures; 95 % percentile intervals"),
    "choice_rule": (
        "index with the highest validation accuracy; if it beats dNBR by less than 1 point, dNBR"),
    "emsn188": (
        "independent check: up to 300 damage points in validation blocks, outside the "
        "measurement 1 sample, minimum distance 100 m (amended 2026-11-11 before any figure was "
        "seen: every point that fits, as only 145 do); share in burned with Wilson 95 %"),
})


# --- Indices -------------------------------------------------------------------------------------

def pre_nbr(pre: dict[str, Path], bbox: tuple[float, float, float, float]) -> np.ndarray:
    """NBR of the pre-fire scene on the study window (unscaled), computed exactly as
    ``calculo.severity`` computes it."""
    w = calculo.window(pre["nir08"], bbox)
    nir, swir, _scl, _water = calculo._reflectance(pre, w)
    return calculo._nbr(nir, swir)


def relative_indices(dnbr: np.ndarray, nbr_pre: np.ndarray) -> dict[str, np.ndarray]:
    """dNBR (as given, x 1000, offset removed), RdNBR and RBR; NaN where dNBR is NaN."""
    if dnbr.shape != nbr_pre.shape:
        raise DataError("dNBR and pre-fire NBR differ in shape")
    usable = np.isfinite(dnbr) & np.isfinite(nbr_pre)
    pre = np.where(usable, nbr_pre, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        rdnbr = dnbr / np.sqrt(np.maximum(np.abs(pre), RDNBR_MIN_PRE))
        rbr = dnbr / (pre + RBR_ADD)
    nan = np.array(np.nan)
    return {"dNBR": np.where(usable, dnbr, nan), "RdNBR": np.where(usable, rdnbr, nan),
            "RBR": np.where(usable, rbr, nan)}


# --- Blocks --------------------------------------------------------------------------------------

def block_ids(shape: tuple[int, int], size: int = BLOCK_PX) -> np.ndarray:
    """Block number of every pixel (row-major grid of ``size`` x ``size`` pixels)."""
    rows, cols = shape
    per_row = -(-cols // size)
    r = np.arange(rows)[:, None] // size
    c = np.arange(cols)[None, :] // size
    return (r * per_row + c).astype(np.int64)


def assign_blocks(blocks: np.ndarray, domain: np.ndarray, reference: np.ndarray,
                  rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """(fit, validation) block numbers. Blocks with domain pixels are sorted by reference burned
    fraction (then by number) and split in consecutive pairs, one to each side at random; an odd
    last block goes to either side at random."""
    ids = blocks[domain]
    n = int(blocks.max()) + 1
    pixels = np.bincount(ids, minlength=n)
    burned = np.bincount(ids, weights=reference[domain].astype(np.float64), minlength=n)
    present = np.flatnonzero(pixels)
    fraction = burned[present] / pixels[present]
    order = present[np.lexsort((present, fraction))]
    fit: list[int] = []
    validation: list[int] = []
    for i in range(0, len(order), 2):
        pair = order[i:i + 2]
        if len(pair) == 2:
            first = int(rng.integers(2))
            fit.append(int(pair[first]))
            validation.append(int(pair[1 - first]))
        elif rng.integers(2) == 0:
            fit.append(int(pair[0]))
        else:
            validation.append(int(pair[0]))
    return np.asarray(sorted(fit), dtype=np.int64), np.asarray(sorted(validation), dtype=np.int64)


def guard_band(area: np.ndarray, min_distance_px: int) -> np.ndarray:
    """Pixels whose Chebyshev distance to ``area`` is below ``min_distance_px``."""
    if min_distance_px < 1:
        return area.copy()
    near: np.ndarray = maximum_filter(area.astype(np.uint8), size=2 * min_distance_px - 1,
                                      mode="constant", cval=0).astype(bool)
    return near


def split_pixels(domain: np.ndarray, blocks: np.ndarray, fit: np.ndarray,
                 validation: np.ndarray, excluded: np.ndarray) -> tuple[np.ndarray, np.ndarray,
                                                                       np.ndarray]:
    """(fit pixels, validation pixels, guard band around the fit blocks). Excluded pixels are in
    neither set; validation pixels keep the measurement 2 distance from every fit block."""
    fit_area = np.isin(blocks, fit)
    near_fit = guard_band(fit_area, estudio.M2_MIN_DISTANCE_PX)
    fit_px = domain & fit_area & ~excluded
    val_px = domain & np.isin(blocks, validation) & ~near_fit & ~excluded
    return fit_px, val_px, near_fit


# --- Thresholds ----------------------------------------------------------------------------------

def is_burned(values: np.ndarray, threshold: int) -> np.ndarray:
    """A pixel is burned when its index is at least the threshold (NaN is never burned)."""
    out: np.ndarray = values >= threshold
    return out


def bins(values: np.ndarray, grid: tuple[int, int] = GRID) -> np.ndarray:
    """Bin of each value: bin k holds [low + k, low + k + 1); the end bins hold what lies out."""
    low, high = grid
    return (np.clip(np.floor(values), low, high) - low).astype(np.int64)


def block_histograms(values: np.ndarray, reference: np.ndarray, blocks: np.ndarray,
                     chosen: np.ndarray, grid: tuple[int, int] = GRID) -> tuple[np.ndarray,
                                                                               np.ndarray]:
    """(burned, unburned) reference counts per chosen block (rows, in ``chosen`` order) and bin.
    ``values``, ``reference`` and ``blocks`` are 1-D, one entry per pixel."""
    n_bins = grid[1] - grid[0] + 1
    row = np.full(int(blocks.max(initial=0)) + 1, -1, dtype=np.int64)
    row[chosen] = np.arange(len(chosen))
    r = row[blocks]
    keep = r >= 0
    flat = r[keep] * n_bins + bins(values[keep], grid)
    size = len(chosen) * n_bins
    ref = reference[keep]
    burned = np.bincount(flat[ref], minlength=size).reshape(len(chosen), n_bins)
    unburned = np.bincount(flat[~ref], minlength=size).reshape(len(chosen), n_bins)
    return burned, unburned


def _curves(burned: np.ndarray, unburned: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Burned and unburned counts at or above each candidate bin k (last axis, k = 0..bins)."""
    zeros = np.zeros((*burned.shape[:-1], 1))
    above_b = np.concatenate([np.cumsum(burned[..., ::-1], axis=-1)[..., ::-1], zeros], axis=-1)
    above_u = np.concatenate([np.cumsum(unburned[..., ::-1], axis=-1)[..., ::-1], zeros],
                             axis=-1)
    return above_b, above_u


def best_bin(burned: np.ndarray, unburned: np.ndarray, criterion: str = "accuracy") -> np.ndarray:
    """Candidate bin k (burned when bin >= k) that best separates the classes, for each row of
    the last axis. Candidates exclude the two ends of the grid; ties go to the lowest k."""
    above_b, above_u = _curves(burned.astype(np.float64), unburned.astype(np.float64))
    total_b = above_b[..., :1]
    total_u = above_u[..., :1]
    if criterion == "accuracy":
        score = above_b + (total_u - above_u)
    elif criterion == "youden":
        with np.errstate(divide="ignore", invalid="ignore"):
            score = above_b / total_b - above_u / total_u
    elif criterion == "balance":
        score = -np.abs(above_b + above_u - total_b)
    else:
        raise DataError(f"unknown criterion {criterion!r}")
    inner = score[..., 1:-1]
    return np.argmax(inner, axis=-1) + 1


def threshold_of(k: int, grid: tuple[int, int] = GRID) -> int:
    """Threshold (index units x 1000) of candidate bin k."""
    return int(k) + grid[0]


def bootstrap_weights(n: int, replicates: int, rng: np.random.Generator) -> np.ndarray:
    """How many times each of ``n`` blocks is drawn in each replicate (rows)."""
    draws = rng.integers(0, n, size=(replicates, n))
    return np.stack([np.bincount(row, minlength=n) for row in draws]).astype(np.float64)


def interval(values: np.ndarray) -> list[float]:
    """95 % percentile interval (empirical quantiles, no interpolation)."""
    lo, hi = np.quantile(values, [0.025, 0.975], method="inverted_cdf")
    return [float(lo), float(hi)]


# --- Validation ----------------------------------------------------------------------------------

def block_confusion(predicted: np.ndarray, reference: np.ndarray, blocks: np.ndarray,
                    chosen: np.ndarray) -> np.ndarray:
    """Counts per chosen block: columns (burned in both, predicted only, reference only,
    unburned in both). All inputs are 1-D, one entry per pixel."""
    row = np.full(int(blocks.max(initial=0)) + 1, -1, dtype=np.int64)
    row[chosen] = np.arange(len(chosen))
    r = row[blocks]
    keep = r >= 0
    cell = np.where(predicted[keep], np.where(reference[keep], 0, 1),
                    np.where(reference[keep], 2, 3))
    out: np.ndarray = np.bincount(r[keep] * 4 + cell, minlength=len(chosen) * 4).reshape(-1, 4)
    return out


def rates(counts: np.ndarray) -> dict[str, np.ndarray]:
    """Accuracy, commission, omission and area difference (% of the reference) from summed
    counts (last axis: both, predicted only, reference only, neither)."""
    tp, fp, fn, tn = (counts[..., i].astype(np.float64) for i in range(4))
    with np.errstate(divide="ignore", invalid="ignore"):
        return {"accuracy_pct": 100 * (tp + tn) / (tp + fp + fn + tn),
                "commission_pct": 100 * fp / (tp + fp),
                "omission_pct": 100 * fn / (tp + fn),
                "area_difference_pct": 100 * (fp - fn) / (tp + fn)}


def _r1(value: float) -> float:
    return round(float(value), 1)


def _ha(pixels: float, pixel_ha: float) -> float:
    return _r1(pixels * pixel_ha)


def validation_figures(counts: np.ndarray, weights: np.ndarray,
                       pixel_ha: float) -> dict[str, Any]:
    """Validation figures of one threshold: census values with block-bootstrap intervals."""
    total = counts.sum(axis=0)
    point = rates(total)
    replicated = rates(weights @ counts)
    out: dict[str, Any] = {"pixels": [int(v) for v in total],
                           "predicted_burned_ha": _ha(total[0] + total[1], pixel_ha)}
    for name, value in point.items():
        out[name] = _r1(float(value))
        out[f"{name.removesuffix('_pct')}_interval"] = [_r1(v) for v in interval(replicated[name])]
    return out


def paired_difference(counts_a: np.ndarray, counts_b: np.ndarray,
                      weights: np.ndarray) -> list[float]:
    """Accuracy of a minus accuracy of b (points), census value and bootstrap interval."""
    point = float(rates(counts_a.sum(axis=0))["accuracy_pct"]
                  - rates(counts_b.sum(axis=0))["accuracy_pct"])
    replicated = (rates(weights @ counts_a)["accuracy_pct"]
                  - rates(weights @ counts_b)["accuracy_pct"])
    lo, hi = interval(replicated)
    return [_r1(point), _r1(lo), _r1(hi)]


def choose_index(accuracy: Mapping[str, float], tie_points: float) -> str:
    """Rule fixed before computing: the most accurate index in validation, unless it beats dNBR
    by less than ``tie_points``."""
    best = max(INDICES, key=lambda name: (accuracy[name], -INDICES.index(name)))
    if accuracy[best] - accuracy["dNBR"] < tie_points:
        return "dNBR"
    return best


# --- Rebuilding the published samples ------------------------------------------------------------

def m2_pixels(classes: np.ndarray, domain: np.ndarray, reference: np.ndarray,
              published: Mapping[str, Any]) -> np.ndarray:
    """Mask of the pixels sampled by measurement 2, rebuilt with the study seed. Raises if the
    rebuilt sample does not give the published confusion matrix."""
    mask = np.zeros(classes.shape, dtype=bool)
    ref = reference.ravel()
    for key, code in estudio.THRESHOLDS.items():
        predicted = classes >= code
        burned, unburned = validacion.sample_strata(predicted, domain, estudio.SAMPLE_SIZE,
                                                    estudio.SEED, estudio.M2_MIN_DISTANCE_PX)
        matrix = [[int(ref[burned].sum()), int(burned.size - ref[burned].sum())],
                  [int(ref[unburned].sum()), int(unburned.size - ref[unburned].sum())]]
        if matrix != published[key]["matrix"]:
            raise DataError(f"measurement 2, threshold {key}: rebuilt sample gives {matrix}, "
                            f"published {published[key]['matrix']}")
        mask.ravel()[burned] = True
        mask.ravel()[unburned] = True
    return mask


def m1_sample(classes: np.ndarray, transform: Affine, in_area: np.ndarray, xy: np.ndarray,
              published: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """(sample indices, universe indices) of measurement 1, rebuilt with the study seed. Raises if
    the rebuilt sample does not give the published counts."""
    class_at, on_grid = validacion.values_at(xy, classes, transform)
    zone_at, _ = validacion.values_at(xy, in_area.astype(np.uint8), transform)
    universe = np.flatnonzero(on_grid & (zone_at > 0) & (class_at > 0))
    sample = universe[validacion.sample_points(xy[universe], estudio.SAMPLE_SIZE,
                                               estudio.M1_MIN_DISTANCE_M, estudio.SEED)]
    for key, code in estudio.THRESHOLDS.items():
        hits = int((class_at[sample] >= code).sum())
        if hits != published[key]["in_burned_class"]:
            raise DataError(f"measurement 1, threshold {key}: rebuilt sample gives {hits}, "
                            f"published {published[key]['in_burned_class']}")
    return sample, universe


# --- The adjustment ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Inputs:
    """Everything the adjustment needs, on the study grid."""
    classes: np.ndarray
    indices: dict[str, np.ndarray]
    transform: Affine
    crs: str
    in_area: np.ndarray
    reference: np.ndarray
    footprint: np.ndarray
    damage_xy: np.ndarray
    damage_grades: np.ndarray


def adjust(inputs: Inputs, metrics_data: Mapping[str, Any]) -> dict[str, Any]:
    """Content of ``ajuste_umbrales.json``."""
    classes, reference = inputs.classes, inputs.reference
    pixel_ha = abs(inputs.transform.a * inputs.transform.e) / 10_000.0
    domain = (classes > 0) & inputs.in_area & inputs.footprint
    m2 = metrics_data["measurement_2"]
    for name, mine in (("domain_ha", _ha(domain.sum(), pixel_ha)),
                       ("reference_burned_ha", _ha((domain & reference).sum(), pixel_ha))):
        if mine != m2[name]:
            raise DataError(f"{name}: {mine} here, {m2[name]} in {estudio.METRICS_FILE}")
    for name, values in inputs.indices.items():
        if not np.isfinite(values[domain]).all():
            raise DataError(f"{name} is not defined on every domain pixel")

    excluded = m2_pixels(classes, domain, reference, m2)
    blocks = block_ids(classes.shape)
    fit, validation = assign_blocks(blocks, domain, reference,
                                    np.random.default_rng((estudio.SEED, STREAMS["blocks"])))
    fit_px, val_px, near_fit = split_pixels(domain, blocks, fit, validation, excluded)
    val_blocks = np.intersect1d(validation, np.unique(blocks[val_px]))

    fit_weights = bootstrap_weights(len(fit), BOOTSTRAP_REPLICATES, np.random.default_rng(
        (estudio.SEED, STREAMS["fit_bootstrap"])))
    val_weights = bootstrap_weights(len(val_blocks), BOOTSTRAP_REPLICATES, np.random.default_rng(
        (estudio.SEED, STREAMS["validation_bootstrap"])))

    ref_val = reference[val_px]
    blk_val = blocks[val_px]
    counts: dict[str, np.ndarray] = {}
    for key, code in estudio.THRESHOLDS.items():
        counts[key] = block_confusion(classes[val_px] >= code, ref_val, blk_val, val_blocks)

    results: dict[str, Any] = {}
    for name in INDICES:
        values = inputs.indices[name]
        hb, hu = block_histograms(values[fit_px], reference[fit_px], blocks[fit_px], fit)
        point = int(best_bin(hb.sum(axis=0), hu.sum(axis=0)))
        if point in (1, hb.shape[1] - 1):
            raise DataError(f"{name}: best threshold at the edge of the grid {GRID}")
        replicated = best_bin(fit_weights @ hb, fit_weights @ hu)
        threshold = int(threshold_of(point))
        dom_b, dom_u = block_histograms(values[domain], reference[domain],
                                        np.zeros(int(domain.sum()), dtype=np.int64),
                                        np.zeros(1, dtype=np.int64))
        above_b, above_u = _curves(dom_b[0].astype(np.float64), dom_u[0].astype(np.float64))
        hectares = (above_b + above_u) * pixel_ha
        counts[name] = block_confusion(is_burned(values[val_px], threshold), ref_val, blk_val,
                                       val_blocks)
        results[name] = {
            "threshold": threshold,
            "threshold_interval": [int(v) for v in interval(replicated + GRID[0])],
            "hectares": _r1(hectares[point]),
            "hectares_interval": [_r1(v) for v in interval(hectares[replicated])],
            "sensitivity_thresholds": {
                criterion: int(threshold_of(int(best_bin(hb.sum(axis=0), hu.sum(axis=0),
                                                         criterion))))
                for criterion in ("youden", "balance")},
            "validation": validation_figures(counts[name], val_weights, pixel_ha),
        }

    chosen = choose_index({n: results[n]["validation"]["accuracy_pct"] for n in INDICES},
                          estudio.HEADLINE_TIE_POINTS)
    published = {key: validation_figures(counts[key], val_weights, pixel_ha)
                 for key in estudio.THRESHOLDS}
    differences = {f"{name}_minus_{other}": paired_difference(counts[name], counts[other],
                                                              val_weights)
                   for name in INDICES for other in ("A", "B", "dNBR") if name != other}

    return {
        "note": NOTE,
        "date": ADJUSTMENT_DATE,
        "protocol": {**PROTOCOL, "seed": estudio.SEED, "block_px": BLOCK_PX,
                     "replicates": BOOTSTRAP_REPLICATES},
        "domain": {
            "domain_ha": m2["domain_ha"], "reference_burned_ha": m2["reference_burned_ha"],
            "m2_pixels_excluded": int(excluded.sum()),
            "fit_blocks": len(fit), "validation_blocks": len(val_blocks),
            "fit_ha": _ha(fit_px.sum(), pixel_ha),
            "fit_reference_burned_ha": _ha((fit_px & reference).sum(), pixel_ha),
            "validation_ha": _ha(val_px.sum(), pixel_ha),
            "validation_reference_burned_ha": _ha((val_px & reference).sum(), pixel_ha),
        },
        "indices": results,
        "published_thresholds_on_validation": published,
        "accuracy_differences_points": differences,
        "chosen": {"index": chosen, "threshold": results[chosen]["threshold"],
                   "threshold_interval": results[chosen]["threshold_interval"],
                   "hectares": results[chosen]["hectares"],
                   "hectares_interval": results[chosen]["hectares_interval"],
                   "headline_unchanged": dict(metrics_data["headline"])},
        "emsn188": emsn188_check(inputs, validation, blocks, near_fit, results, metrics_data),
    }


def emsn188_check(inputs: Inputs, validation: np.ndarray, blocks: np.ndarray,
                  near_fit: np.ndarray, results: Mapping[str, Any],
                  metrics_data: Mapping[str, Any]) -> dict[str, Any]:
    """Share of EMSN188 damage points in burned, on a new sample in validation blocks."""
    xy, grades = inputs.damage_xy, inputs.damage_grades
    m1_taken, universe = m1_sample(inputs.classes, inputs.transform, inputs.in_area, xy,
                                   metrics_data["measurement_1"])
    region = np.isin(blocks, validation) & ~near_fit
    region_at, _ = validacion.values_at(xy, region.astype(np.uint8), inputs.transform)
    candidates = emsn188_candidates(universe, region_at, m1_taken)
    sample = emsn188_sample(xy, candidates)
    if len(sample) == 0:
        raise DataError("EMSN188: no damage point in the validation blocks")
    destroyed = sample[grades[sample] == "3"]
    rules: dict[str, np.ndarray] = {}
    class_at, _ = validacion.values_at(xy, inputs.classes, inputs.transform)
    for key, code in estudio.THRESHOLDS.items():
        rules[key] = class_at >= code
    for name in INDICES:
        value_at = _float_at(xy, inputs.indices[name], inputs.transform)
        rules[name] = is_burned(value_at, results[name]["threshold"])
    out: dict[str, Any] = {"candidates": len(candidates), "sample_target": EMSN188_SAMPLE,
                           "sample": len(sample), "destroyed": len(destroyed)}
    for name, burned in rules.items():
        p = validacion.wilson(int(burned[sample].sum()), len(sample))
        d = validacion.wilson(int(burned[destroyed].sum()), len(destroyed))
        out[name] = {"in_burned": p.successes, "pct": _r1(100 * p.value),
                     "wilson": [_r1(100 * p.low), _r1(100 * p.high)],
                     "destroyed_in_burned": d.successes, "destroyed_pct": _r1(100 * d.value),
                     "destroyed_wilson": [_r1(100 * d.low), _r1(100 * d.high)]}
    return out


def emsn188_candidates(universe: np.ndarray, region_at: np.ndarray,
                       taken: np.ndarray) -> np.ndarray:
    """Damage points of the measurement 1 universe inside the validation region and not in the
    measurement 1 sample (sorted indices)."""
    return np.setdiff1d(universe[region_at[universe] > 0], taken)


def emsn188_sample(xy: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """Up to ``EMSN188_SAMPLE`` candidates, measurement 1 spacing and seed."""
    chosen: np.ndarray = candidates[validacion.sample_points(
        xy[candidates], EMSN188_SAMPLE, estudio.M1_MIN_DISTANCE_M, estudio.SEED)]
    return chosen


def _float_at(xy: np.ndarray, raster: np.ndarray, transform: Affine) -> np.ndarray:
    """Value of the pixel that contains each point; NaN outside the raster."""
    col, row = ~transform * (xy[:, 0], xy[:, 1])
    row, col = np.floor(row).astype(int), np.floor(col).astype(int)
    inside = (row >= 0) & (row < raster.shape[0]) & (col >= 0) & (col < raster.shape[1])
    out = np.full(len(xy), np.nan)
    out[inside] = raster[row[inside], col[inside]]
    return out


# --- Running it ----------------------------------------------------------------------------------

def load_inputs(raw: Path, published: Path) -> Inputs:
    """Scenes, references and boundaries from ``raw``; checks that the classes rebuilt here are
    the published ``clases.tif`` pixel for pixel."""
    raw, published = Path(raw), Path(published)
    refs = estudio.check_references(raw / estudio.REFERENCES_FOLDER)
    bands = estudio.scenes(raw)
    result = estudio.severity(bands)
    with rasterio.open(published / estudio.CLASSES_FILE) as r:
        same = (np.array_equal(r.read(1), result.classes) and r.transform == result.transform
                and r.crs.to_string() == result.crs)
    if not same:
        raise DataError(f"the classes rebuilt from {raw} are not {estudio.CLASSES_FILE}")
    nbr = pre_nbr(bands[estudio.PRE_SCENE], estudio.BBOX)
    municipalities = estudio.load_municipalities(raw, download=False)
    in_area = validacion.zone_mask(municipalities, result.crs, result.transform,
                                   result.classes.shape)
    burned, footprint = validacion.emsr715_delineation(refs["EMSR715"], result.crs)
    shape = result.classes.shape
    reference = rasterize([(g, 1) for g in burned], out_shape=shape, transform=result.transform,
                          fill=0, dtype="uint8").astype(bool)
    in_footprint = rasterize([(unary_union(footprint), 1)], out_shape=shape,
                             transform=result.transform, fill=0, dtype="uint8").astype(bool)
    xy, grades = validacion.emsn188_damage(refs["EMSN188"], result.crs)
    return Inputs(result.classes, relative_indices(result.dnbr, nbr), result.transform,
                  result.crs, in_area, reference, in_footprint, xy, grades)


def run(out: Path, raw: Path, published: Path) -> Path:
    """The adjustment from scratch into a new folder ``out`` (never into ``datos/``)."""
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} exists: choose a new folder, results are never overwritten")
    metrics_data = json.loads((Path(published) / estudio.METRICS_FILE).read_text(encoding="utf-8"))
    data = adjust(load_inputs(raw, published), metrics_data)
    out.mkdir(parents=True)
    path = estudio.write_json(data, out / ADJUSTMENT_FILE)
    logger.info("adjustment written to %s", path)
    return path


def summary(data: Mapping[str, Any]) -> list[str]:
    """Plain lines with the main figures, for the command output."""
    lines = [f"domain {data['domain']['domain_ha']} ha, EMSR715 burned "
             f"{data['domain']['reference_burned_ha']} ha; blocks fit "
             f"{data['domain']['fit_blocks']}, validation {data['domain']['validation_blocks']}"]
    for name in INDICES:
        r = data["indices"][name]
        v = r["validation"]
        lines.append(f"{name}: threshold {r['threshold']} {r['threshold_interval']}, "
                     f"{r['hectares']} ha {r['hectares_interval']}; validation accuracy "
                     f"{v['accuracy_pct']} {v['accuracy_interval']}, commission "
                     f"{v['commission_pct']}, omission {v['omission_pct']}")
    for key, v in data["published_thresholds_on_validation"].items():
        lines.append(f"{key} on validation: accuracy {v['accuracy_pct']} {v['accuracy_interval']}"
                     f", commission {v['commission_pct']}, omission {v['omission_pct']}")
    c = data["chosen"]
    lines.append(f"chosen {c['index']}: {c['threshold']}, {c['hectares']} ha "
                 f"{c['hectares_interval']}")
    return lines
