"""Validation of the severity map against two Copernicus EMS references.

Measurement 1 (omission in the urban interface): building damage points of EMSN188 (Pleiades Neo,
0.3 m). A simple random sample with a minimum distance between points; the result is the share
that falls in a burned class, with a Wilson interval.

Measurement 2 (burned / unburned): the delineation of EMSR715 (WorldView-2, 2 m). A sample
stratified by the predicted class, with a minimum distance between points to soften spatial
autocorrelation (neighbouring pixels are not independent observations). Overall accuracy and
commission carry a Wilson interval; the accuracy of the whole map and the omission are estimated
by weighting each stratum by its area (Olofsson et al. 2014), which is not a binomial proportion,
so they are reported without an interval.

The references are read straight from the ZIP files the reader downloads; nothing is extracted.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
from affine import Affine
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform as reproject

from .errors import DataError

CHUNK = 1 << 20


@dataclass(frozen=True)
class Proportion:
    successes: int
    n: int
    low: float
    high: float

    @property
    def value(self) -> float:
        return self.successes / self.n

    def __str__(self) -> str:
        return (f"{self.successes}/{self.n} = {100 * self.value:.1f} % "
                f"(Wilson 95 %: {100 * self.low:.1f} to {100 * self.high:.1f})")


def wilson(successes: int, n: int, confidence: float = 0.95) -> Proportion:
    """Wilson score interval (Wilson 1927) for successes / n."""
    if n <= 0:
        raise DataError("n must be positive")
    if not 0 <= successes <= n:
        raise DataError(f"successes={successes} outside 0..{n}")
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denominator
    margin = z * np.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denominator
    low = 0.0 if successes == 0 else max(0.0, centre - margin)
    high = 1.0 if successes == n else min(1.0, centre + margin)
    return Proportion(successes, n, float(low), float(high))


def sample_points(xy: np.ndarray, n: int, min_distance: float, seed: int) -> np.ndarray:
    """Indices of up to `n` points, visited in a seeded random order; a point is kept only if
    its Euclidean distance to every point already kept is `min_distance` or more."""
    rng = np.random.default_rng(seed)
    kept: list[int] = []
    for i in rng.permutation(len(xy)):
        if kept and np.hypot(*(xy[kept] - xy[i]).T).min() < min_distance:
            continue
        kept.append(int(i))
        if len(kept) == n:
            break
    return np.asarray(kept, dtype=int)


def _sample_stratum(candidates: np.ndarray, n: int, width: int, min_distance_px: int,
                    rng: np.random.Generator) -> np.ndarray:
    """Up to `n` flat indices; a candidate is rejected when another kept pixel is closer than
    `min_distance_px` in both rows and columns (Chebyshev distance)."""
    kept: list[int] = []
    rows: list[int] = []
    cols: list[int] = []
    for i in rng.permutation(candidates):
        r, c = divmod(int(i), width)
        if min_distance_px > 0 and rows:
            near = ((np.abs(np.asarray(rows) - r) < min_distance_px)
                    & (np.abs(np.asarray(cols) - c) < min_distance_px))
            if near.any():
                continue
        kept.append(int(i))
        rows.append(r)
        cols.append(c)
        if len(kept) == n:
            break
    return np.asarray(kept, dtype=np.intp)


def sample_strata(predicted: np.ndarray, valid: np.ndarray, n_per_stratum: int, seed: int,
                  min_distance_px: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Flat indices of the (burned, unburned) strata. One generator, burned stratum first.
    Raises ValueError if a stratum cannot reach `n_per_stratum`: a smaller sample than the one
    declared would change the intervals."""
    if predicted.shape != valid.shape or predicted.ndim != 2:
        raise DataError("prediction and validity must be 2-D arrays of the same shape")
    if n_per_stratum < 1 or min_distance_px < 0:
        raise DataError("n_per_stratum must be positive and min_distance_px not negative")
    rng = np.random.default_rng(seed)
    out = []
    for name, stratum in (("burned", predicted & valid), ("unburned", ~predicted & valid)):
        chosen = _sample_stratum(np.flatnonzero(stratum), n_per_stratum, predicted.shape[1],
                                 min_distance_px, rng)
        if chosen.size < n_per_stratum:
            raise DataError(f"{name} stratum: {chosen.size} of {n_per_stratum} points with a "
                             f"minimum distance of {min_distance_px} px")
        out.append(chosen)
    return out[0], out[1]


@dataclass(frozen=True)
class Agreement:
    overall: Proportion
    weighted_accuracy: float
    commission: Proportion
    weighted_omission: float | None
    # rows predicted (burned, unburned), columns reference (burned, unburned)
    matrix: tuple[tuple[int, int], tuple[int, int]]


def agreement(predicted: np.ndarray, reference: np.ndarray, valid: np.ndarray,
              n_per_stratum: int, seed: int, min_distance_px: int = 0) -> Agreement:
    """Agreement of a burned / unburned prediction with a reference on a stratified sample."""
    if reference.shape != predicted.shape:
        raise DataError("reference and prediction differ in shape")
    burned, unburned = sample_strata(predicted, valid, n_per_stratum, seed, min_distance_px)
    ref = reference.ravel()
    bb = int(ref[burned].sum())
    bu = int(burned.size - bb)
    ub = int(ref[unburned].sum())
    uu = int(unburned.size - ub)
    area_b = float((predicted & valid).sum())
    area_u = float((~predicted & valid).sum())
    w = area_b / (area_b + area_u)
    weighted = w * bb / burned.size + (1.0 - w) * uu / unburned.size
    ref_in_b = w * bb / burned.size
    ref_in_u = (1.0 - w) * ub / unburned.size
    omission = ref_in_u / (ref_in_b + ref_in_u) if ref_in_b + ref_in_u > 0 else None
    return Agreement(overall=wilson(bb + uu, burned.size + unburned.size),
                     weighted_accuracy=weighted, commission=wilson(bu, burned.size),
                     weighted_omission=omission, matrix=((bb, bu), (ub, uu)))


def zone_mask(geojson: dict[str, Any], crs: str, transform: Affine,
              shape_: tuple[int, int]) -> np.ndarray:
    """Pixels whose centre falls in any feature of a GeoJSON FeatureCollection (EPSG:4326)."""
    from rasterio.features import rasterize

    to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    shapes = [(reproject(to_crs, shape(f["geometry"])), 1) for f in geojson["features"]]
    if not shapes:
        raise DataError("the zone collection is empty")
    mask: np.ndarray = rasterize(shapes, out_shape=shape_, transform=transform, fill=0,
                                 dtype="uint8").astype(bool)
    return mask


def values_at(xy: np.ndarray, raster: np.ndarray,
              transform: Affine) -> tuple[np.ndarray, np.ndarray]:
    """(value, inside) of the pixel that contains each point; value is 0 outside the raster."""
    col, row = ~transform * (xy[:, 0], xy[:, 1])
    row, col = np.floor(row).astype(int), np.floor(col).astype(int)
    inside = (row >= 0) & (row < raster.shape[0]) & (col >= 0) & (col < raster.shape[1])
    values = np.zeros(len(xy), dtype=raster.dtype)
    values[inside] = raster[row[inside], col[inside]]
    return values, inside


def check_file(path: Path, size: int, sha256: str) -> None:
    """Raise if `path` is missing or is not the published file (size and sha256)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} not found: download it as the notebook explains")
    if path.stat().st_size != size:
        raise DataError(f"{path.name}: {path.stat().st_size} bytes, expected {size}")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    if h.hexdigest() != sha256:
        raise DataError(f"{path.name}: sha256 {h.hexdigest()[:12]}..., expected {sha256[:12]}...")


def _inner_bytes(outer: Path, inner_name: str) -> bytes:
    """Bytes of an inner file of the EMSR715 package. The package repeats some names; every copy
    must be identical, otherwise the reference is ambiguous."""
    with zipfile.ZipFile(outer) as z:
        copies = {}
        for info in z.infolist():
            if info.filename == inner_name:
                data = z.read(info)
                copies[hashlib.sha256(data).hexdigest()] = data
    if not copies:
        raise FileNotFoundError(f"{inner_name} is not in {Path(outer).name}")
    if len(copies) > 1:
        raise DataError(f"{inner_name} appears with different contents")
    return next(iter(copies.values()))


def inner_sha256(outer: Path, inner_name: str) -> str:
    """sha256 of an inner file of a ZIP package (used when the outer ZIP was repackaged)."""
    return hashlib.sha256(_inner_bytes(outer, inner_name)).hexdigest()


def _inner_zip(outer: Path, inner_name: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(_inner_bytes(outer, inner_name)))


def emsr715_delineation(package: Path, crs: str, product: str = "EMSR715_AOI01_DEL_PRODUCT",
                        version: str = "v2", post_source: str = "2") -> tuple[list[Any], list[Any]]:
    """(burned polygons, footprint of the post-event image) of an EMSR715 delineation product,
    reprojected to `crs`. `post_source` is the `or_src_id` of the post-event image."""
    with _inner_zip(package, f"{product}_{version}.zip") as z:
        burned = json.loads(z.read(f"{product}_observedEventA_{version}.json"))
        footprint = json.loads(z.read(f"{product}_imageFootprintA_{version}.json"))
    to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    polygons = [reproject(to_crs, shape(f["geometry"])) for f in burned["features"]]
    image = [reproject(to_crs, shape(f["geometry"])) for f in footprint["features"]
             if str(f["properties"]["or_src_id"]) == post_source]
    if not polygons or not image:
        raise DataError(f"{product}_{version}: no burned polygons or no post-event footprint")
    return polygons, image


EMSN188_GDB = "Geospatial_data/GDB/EMSN188_STD_UTM19S_r02_v01.gdb"
EMSN188_POINTS = "P08DDA_DamageAssessmentP"
# Grades as reported in the EMSN188 technical report (the geodatabase domain lists four grades
# that were not used): 3 Destroyed, 2 Damaged, 1 Possible damage.
DAMAGE_GRADES = {"3": "Destroyed", "2": "Damaged", "1": "Possible damage"}


def emsn188_damage(package: Path, crs: str) -> tuple[np.ndarray, np.ndarray]:
    """(x, y) of the EMSN188 building damage points and their grade codes, in `crs`."""
    import pyogrio
    from shapely import from_wkb

    path = f"/vsizip/{Path(package).resolve().as_posix()}/{EMSN188_GDB}"
    meta, _fids, geometry, fields = pyogrio.raw.read(path, layer=EMSN188_POINTS,
                                                     columns=["damage_gra"])
    if not CRS.from_user_input(meta["crs"]).equals(CRS.from_user_input(crs)):
        raise DataError(f"EMSN188 points are in {meta['crs']}, the map is in {crs}")
    points = from_wkb(geometry)
    xy = np.asarray([(p.x, p.y) for p in points], dtype=np.float64)
    grades = np.asarray([str(v) for v in fields[0]])
    return xy, grades
