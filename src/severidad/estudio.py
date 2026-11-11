"""The Vina del Mar 2024 study: its parameters, declared once, and the steps that produce every
published file in ``datos/``.

The parameters were fixed before any result was seen (scene choice, thresholds, sampling
protocol); changing one changes the study, not a setting, and ``datos/`` would no longer match.
The severity class limits are part of the method and live in ``calculo.CLASSES``.

``run`` rebuilds ``clases.tif``, ``superficies.csv``, ``huellas.json`` and ``metricas.json`` from
scratch; ``compare_outputs`` and ``compare_documents`` check a rebuild and the documents against
the published files. The command line (``python -m severidad``) calls these functions.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely.ops import transform as reproject
from shapely.ops import unary_union

from . import calculo, fuente, validacion
from .calculo import CLASSES
from .errors import DataError

logger = logging.getLogger(__name__)

# Scenes (Sentinel-2 L2A, Earth Search ids): least cloud inside the study area in each window.
PRE_SCENE: Final = "S2B_19HBD_20240118_0_L2A"
POST_SCENE: Final = "S2A_19HBD_20240212_0_L2A"
# Processing window, lon/lat (west, south, east, north).
BBOX: Final = (-71.65, -33.20, -71.28, -32.98)
# Study area: Chilean municipalities by CUT code, in reporting order.
MUNICIPALITIES: Final = MappingProxyType({
    "05101": "Valpara\u00edso",
    "05109": "Vi\u00f1a del Mar",
    "05801": "Quilpu\u00e9",
    "05804": "Villa Alemana",
})

# Burned / unburned thresholds on the class code: A = every burned class, B = moderate-low or more.
THRESHOLDS: Final = MappingProxyType({"A": 4, "B": 5})
# Validation protocol (fixed before measuring).
SEED: Final = 20240202
SAMPLE_SIZE: Final = 300                 # points in measurement 1; per stratum in measurement 2
M1_MIN_DISTANCE_M: Final = 100.0         # between EMSN188 damage points (Euclidean)
M2_MIN_DISTANCE_PX: Final = 10           # between sampled pixels (Chebyshev; 200 m at 20 m)
HEADLINE_TIE_POINTS: Final = 1.0         # weighted-accuracy difference below which A is published

# Class names from the method, and the colours used by every map (RGBA; 0 to 255).
CLASS_NAMES: Final = MappingProxyType({code: name for code, name, _low, _high in CLASSES})
PALETTE: Final = MappingProxyType({
    0: (0, 0, 0, 0),            # no data
    1: (26, 152, 80, 200),
    2: (145, 207, 96, 200),
    3: (0, 0, 0, 0),            # unburned: transparent on web maps
    4: (254, 224, 139, 230),
    5: (253, 174, 97, 230),
    6: (244, 109, 67, 230),
    7: (165, 0, 38, 230),
})
# Colour of "unburned" where it cannot be transparent (paper, static figures).
UNBURNED_ON_PAPER: Final = (239, 239, 239)


def hex_colour(code: int) -> str:
    """Opaque hex colour of a class for static figures ("unburned" in light grey)."""
    r, g, b = UNBURNED_ON_PAPER if code == 3 else PALETTE[code][:3]
    return f"#{r:02x}{g:02x}{b:02x}"


# --- Published files -----------------------------------------------------------------------------
# The texts below are part of the published files in ``datos/``; they describe this module's
# protocol in plain words and change only if the study changes.

REFERENCES: Final[Mapping[str, Mapping[str, str | int]]] = MappingProxyType({
    "EMSR715": MappingProxyType({
        "file": "EMSR715_products.zip",
        "bytes": 178149313,
        "sha256": "aa31440615508ba60cba1fe3da0c9e980d308d6547d76a7a0bf21a81b0a25905",
        "product": "EMSR715_AOI01_DEL_PRODUCT_v2.zip",
        "product_bytes": 12884618,
        "product_sha256": "7742f03b8191187757e09d1f318b4001786612bd8b3c223e4023e9a662b67eb8",
    }),
    "EMSN188": MappingProxyType({
        "file": "EMSN188_GeospatialData.zip",
        "bytes": 1326369,
        "sha256": "443e7c353f392c56124004a9ae3b2dfc1dbb1af26d9fd0fef31f610cb5dafed3",
    }),
})
REFERENCES_FOLDER: Final = "referencias"
MUNICIPALITIES_FILE: Final = "comunas.geojson"

FINGERPRINTS_NOTE: Final = (
    "content hash = sha256 of dtype, shape, CRS, transform and pixels "
    "(severidad.calculo.content_hash); identical on Windows and Linux")
METRICS_NOTE: Final = (
    "Validation metrics published with this repository (notebooks/02_validacion.ipynb). The "
    "protocol was fixed before any result was seen; the notebook recomputes every value and "
    "compares it with this file.")
THRESHOLDS_TEXT: Final[Mapping[str, str]] = MappingProxyType({
    "A": "all burned classes (code >= 4)",
    "B": "moderate-low severity or higher (code >= 5)",
})
PROTOCOL_TEXT: Final[Mapping[str, str | Mapping[str, str]]] = MappingProxyType({
    "thresholds": THRESHOLDS_TEXT,
    "measurement_1": (
        "EMSN188 P08 damage points inside the municipalities with valid data; 300 points, "
        "minimum Euclidean distance 100 m; share in a burned class with Wilson 95 %"),
    "measurement_2": (
        "EMSR715 AOI01 delineation v2 inside the municipalities and the WorldView-2 footprint; "
        "300 points per predicted stratum, minimum Chebyshev distance 10 px (200 m); weighted by "
        "stratum area (Olofsson et al. 2014)"),
    "headline_rule": (
        "publish the threshold with the higher area-weighted accuracy against EMSR715 and the "
        "other as a range; if they differ by less than 1 point, publish A"),
})
CLASSES_FILE: Final = "clases.tif"
AREAS_FILE: Final = "superficies.csv"
FINGERPRINTS_FILE: Final = "huellas.json"
METRICS_FILE: Final = "metricas.json"
# Local calibration of the burned / unburned threshold (severidad.ajuste, 2026-11-11). Since that
# date its chosen threshold is the headline of the documents; metricas.json keeps the result of the
# rule fixed before measuring, unchanged, as the record of the original protocol.
ADJUSTMENT_FILE: Final = "ajuste_umbrales.json"
VIEWER_ADJUSTMENT_KEY: Final = "calibrated"
NO_DATA_NAME: Final = "No data"


# --- Orchestration (what notebooks 01 and 02 run) ------------------------------------------------

def _round1(value: float) -> float:
    return round(float(value), 1)


def _pct(p: validacion.Proportion) -> tuple[float, list[float]]:
    return _round1(100 * p.value), [_round1(100 * p.low), _round1(100 * p.high)]


def load_municipalities(raw: Path, download: bool = True) -> dict[str, Any]:
    """Study municipalities as a GeoJSON FeatureCollection (EPSG:4326), read from
    ``raw/comunas.geojson`` or downloaded once from the public MOP service. Not redistributed."""
    local = Path(raw) / MUNICIPALITIES_FILE
    if not local.exists():
        if not download:
            raise DataError(f"{local.name} not found and download disabled")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(fuente.municipalities(dict(MUNICIPALITIES))), encoding="utf-8")
    data: dict[str, Any] = json.loads(local.read_text(encoding="utf-8"))
    codes = {f["properties"]["CUT_COM"] for f in data.get("features", [])}
    if codes != set(MUNICIPALITIES):
        raise DataError(f"{local.name} has municipalities {sorted(codes)}, "
                        f"expected {list(MUNICIPALITIES)}")
    return data


def scenes(raw: Path, bands: Sequence[str] = fuente.BANDS) -> dict[str, dict[str, Path]]:
    """Bands of the two study scenes, downloaded once to ``raw/<scene>/`` (existing files kept).
    The default bands are the ones the study uses; others (``fuente.COLOUR_BANDS``) are only
    drawn and never enter a published figure."""
    return {s: fuente.download_scene(s, Path(raw), bands=bands) for s in (PRE_SCENE, POST_SCENE)}


def severity(bands: dict[str, dict[str, Path]]) -> calculo.Result:
    """Severity classes of the study window."""
    return calculo.severity(bands[PRE_SCENE], bands[POST_SCENE], BBOX)


def area_table(result: calculo.Result,
               municipalities: dict[str, Any]) -> dict[str, dict[int, float]]:
    """Hectares per municipality and class code, municipalities in study order."""
    to_crs = Transformer.from_crs("EPSG:4326", result.crs, always_xy=True).transform
    by_code = {f["properties"]["CUT_COM"]: f for f in municipalities["features"]}
    zones = {name: reproject(to_crs, shape(by_code[code]["geometry"]))
             for code, name in MUNICIPALITIES.items()}
    return calculo.areas(result, zones)


def write_areas(table: dict[str, dict[int, float]], path: Path) -> Path:
    """``superficies.csv``: one row per municipality and class code, hectares with 2 decimals."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    names = {0: NO_DATA_NAME, **CLASS_NAMES}
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["municipality", "class_code", "class_name", "hectares"])
        for municipality, row in table.items():
            for code in sorted(names):
                writer.writerow([municipality, code, names[code], f"{row[code]:.2f}"])
    return path


def fingerprints(classes_path: Path, bands: dict[str, dict[str, Path]]) -> dict[str, Any]:
    """Content hash of the classes raster and sha256 of every input band."""
    return {
        "classes_content_sha256": calculo.content_hash(Path(classes_path)),
        "note": FINGERPRINTS_NOTE,
        "scenes": {scene: {band: fuente.sha256(path) for band, path in paths.items()}
                   for scene, paths in bands.items()},
    }


def check_references(folder: Path) -> dict[str, Path]:
    """Paths of the two Copernicus references after checking they are the published files. A
    repackaged EMSR715 download is accepted when its delineation product is the one used."""
    folder = Path(folder)
    r715, r188 = REFERENCES["EMSR715"], REFERENCES["EMSN188"]
    emsr715, emsn188 = folder / str(r715["file"]), folder / str(r188["file"])
    validacion.check_file(emsn188, int(r188["bytes"]), str(r188["sha256"]))
    try:
        validacion.check_file(emsr715, int(r715["bytes"]), str(r715["sha256"]))
    except DataError as outer_differs:
        if validacion.inner_sha256(emsr715, str(r715["product"])) != r715["product_sha256"]:
            raise DataError(f"{r715['product']} inside {emsr715.name} is not the one used: "
                            f"{outer_differs}") from outer_differs
        logger.warning("%s was repackaged; the delineation product inside is the one used",
                       emsr715.name)
    return {"EMSR715": emsr715, "EMSN188": emsn188}


def measurement_1(classes: np.ndarray, transform: Affine, crs: str, in_area: np.ndarray,
                  emsn188: Path) -> dict[str, Any]:
    """Omission in the urban interface: EMSN188 damage points in a burned class."""
    xy, grades = validacion.emsn188_damage(emsn188, crs)
    class_at, on_grid = validacion.values_at(xy, classes, transform)
    zone_at, _ = validacion.values_at(xy, in_area.astype(np.uint8), transform)
    inside = on_grid & (zone_at > 0)
    universe = np.flatnonzero(inside & (class_at > 0))
    sample = universe[validacion.sample_points(xy[universe], SAMPLE_SIZE, M1_MIN_DISTANCE_M, SEED)]
    destroyed = sample[grades[sample] == "3"]
    out: dict[str, Any] = {
        "points": len(xy), "in_municipalities": int(inside.sum()),
        "in_no_data": int((inside & (class_at == 0)).sum()), "universe": len(universe),
        "sample": len(sample),
        "sample_grades": {name: int((grades[sample] == code).sum())
                          for code, name in validacion.DAMAGE_GRADES.items()},
    }
    for key, code in THRESHOLDS.items():
        p = validacion.wilson(int((class_at[sample] >= code).sum()), len(sample))
        d = validacion.wilson(int((class_at[destroyed] >= code).sum()), len(destroyed))
        pct, interval = _pct(p)
        d_pct, d_interval = _pct(d)
        out[key] = {"in_burned_class": p.successes, "n": p.n, "pct": pct, "wilson": interval,
                    "destroyed_in_burned_class": d.successes, "destroyed_n": d.n,
                    "destroyed_pct": d_pct, "destroyed_wilson": d_interval}
    return out


def measurement_2(classes: np.ndarray, transform: Affine, crs: str, in_area: np.ndarray,
                  emsr715: Path) -> dict[str, Any]:
    """Burned / unburned agreement with the EMSR715 delineation (stratified, area-weighted)."""
    burned, footprint = validacion.emsr715_delineation(emsr715, crs)
    reference = rasterize([(g, 1) for g in burned], out_shape=classes.shape, transform=transform,
                          fill=0, dtype="uint8").astype(bool)
    in_footprint = rasterize([(unary_union(footprint), 1)], out_shape=classes.shape,
                             transform=transform, fill=0, dtype="uint8").astype(bool)
    domain = (classes > 0) & in_area & in_footprint
    ha = abs(transform.a * transform.e) / 10_000.0
    out: dict[str, Any] = {"domain_pixels": int(domain.sum()),
                           "domain_ha": _round1(domain.sum() * ha),
                           "reference_burned_ha": _round1((domain & reference).sum() * ha)}
    for key, code in THRESHOLDS.items():
        predicted = classes >= code
        a = validacion.agreement(predicted, reference, domain, SAMPLE_SIZE, SEED,
                                 M2_MIN_DISTANCE_PX)
        overall_pct, overall_interval = _pct(a.overall)
        commission_pct, commission_interval = _pct(a.commission)
        if a.weighted_omission is None:
            raise DataError(f"threshold {key}: no reference burned area in the sample")
        out[key] = {
            "predicted_burned_ha": _round1((domain & predicted).sum() * ha),
            "overall": [a.overall.successes, a.overall.n], "overall_pct": overall_pct,
            "overall_wilson": overall_interval,
            "weighted_accuracy_pct": _round1(100 * a.weighted_accuracy),
            "commission": [a.commission.successes, a.commission.n],
            "commission_pct": commission_pct, "commission_wilson": commission_interval,
            "weighted_omission_pct": _round1(100 * a.weighted_omission),
            "matrix": [list(row) for row in a.matrix],
        }
    return out


def headline(m2: dict[str, Any]) -> dict[str, Any]:
    """Rule fixed before measuring: the threshold with the higher area-weighted accuracy is the
    headline and the larger area is the top of the range; a tie under 1 point publishes A."""
    a, b = m2["A"]["weighted_accuracy_pct"], m2["B"]["weighted_accuracy_pct"]
    chosen = "A" if abs(a - b) < HEADLINE_TIE_POINTS or a > b else "B"
    return {"threshold": chosen, "hectares": m2[chosen]["predicted_burned_ha"],
            "range_up_to_hectares": max(m2["A"]["predicted_burned_ha"],
                                        m2["B"]["predicted_burned_ha"])}


def metrics(classes_hash: str, m1: dict[str, Any], m2: dict[str, Any]) -> dict[str, Any]:
    """Content of ``metricas.json``."""
    return {
        "note": METRICS_NOTE,
        "protocol": {
            "classes_content_sha256": classes_hash,
            "municipalities": list(MUNICIPALITIES),
            "seed": SEED,
            "thresholds": dict(THRESHOLDS_TEXT),
            "measurement_1": PROTOCOL_TEXT["measurement_1"],
            "measurement_2": PROTOCOL_TEXT["measurement_2"],
            "headline_rule": PROTOCOL_TEXT["headline_rule"],
        },
        "references": {key: dict(value) for key, value in REFERENCES.items()},
        "measurement_1": m1,
        "measurement_2": m2,
        "headline": headline(m2),
    }


def write_json(data: dict[str, Any], path: Path) -> Path:
    """JSON with 2-space indent, UTF-8 and LF line endings on every system (written as bytes:
    text mode would turn them into CRLF on Windows). Refuses to overwrite."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def run(out: Path, raw: Path) -> dict[str, Path]:
    """The whole study from scratch: writes the four published files to a new folder ``out``.
    Downloads what is missing in ``raw`` (scenes, municipalities); the Copernicus references must
    already be in ``raw/referencias`` (they are downloaded by hand, see notebook 02)."""
    out, raw = Path(out), Path(raw)
    if out.exists():
        raise FileExistsError(f"{out} exists: choose a new folder, results are never overwritten")
    refs = check_references(raw / REFERENCES_FOLDER)
    bands = scenes(raw)
    result = severity(bands)
    municipalities = load_municipalities(raw)
    out.mkdir(parents=True)
    classes_path = calculo.write_classes(result, out / CLASSES_FILE)
    write_areas(area_table(result, municipalities), out / AREAS_FILE)
    prints = fingerprints(classes_path, bands)
    write_json(prints, out / FINGERPRINTS_FILE)
    in_area = validacion.zone_mask(municipalities, result.crs, result.transform,
                                   result.classes.shape)
    m1 = measurement_1(result.classes, result.transform, result.crs, in_area, refs["EMSN188"])
    m2 = measurement_2(result.classes, result.transform, result.crs, in_area, refs["EMSR715"])
    write_json(metrics(prints["classes_content_sha256"], m1, m2), out / METRICS_FILE)
    logger.info("study written to %s", out)
    return {name: out / name for name in (CLASSES_FILE, AREAS_FILE, FINGERPRINTS_FILE,
                                           METRICS_FILE)}


# --- Verification ---------------------------------------------------------------------------------

def compare_outputs(out: Path, published: Path) -> list[str]:
    """Differences between a ``run`` folder and the published ``datos/`` (empty if identical).
    The raster is compared by content hash, the CSV and the fingerprints byte by byte, the metrics
    by content (formatting is not part of the result)."""
    out, published = Path(out), Path(published)
    problems: list[str] = []
    for name in (CLASSES_FILE, AREAS_FILE, FINGERPRINTS_FILE, METRICS_FILE):
        if not (out / name).exists():
            problems.append(f"{name}: missing in {out}")
    if problems:
        return problems
    if calculo.content_hash(out / CLASSES_FILE) != calculo.content_hash(published / CLASSES_FILE):
        problems.append(f"{CLASSES_FILE}: content hash differs")
    for name in (AREAS_FILE, FINGERPRINTS_FILE):
        if (out / name).read_bytes() != (published / name).read_bytes():
            problems.append(f"{name}: bytes differ")
    generated = json.loads((out / METRICS_FILE).read_text(encoding="utf-8"))
    if generated != json.loads((published / METRICS_FILE).read_text(encoding="utf-8")):
        problems.append(f"{METRICS_FILE}: content differs")
    return problems


def _number(value: float, spanish: bool) -> str:
    text = f"{value:,.1f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".") if spanish else text


VIEWER_FILE: Final = "docs/index.html"
VIEWER_BLOCK: Final = re.compile(r"\bconst\s+DATA\s*=\s*")
VIEWER_KEYS: Final[Mapping[str, str]] = MappingProxyType({
    "headline": "headline", "m1": "measurement_1", "m2": "measurement_2"})


def read_areas(path: Path) -> dict[str, dict[int, float]]:
    """``superficies.csv`` back as hectares per municipality and class code, in file order."""
    table: dict[str, dict[int, float]] = {}
    with Path(path).open(encoding="utf-8", newline="") as f:
        for record in csv.DictReader(f):
            areas = table.setdefault(str(record["municipality"]), {})
            areas[int(record["class_code"])] = float(record["hectares"])
    return table


def _reject_constant(name: str) -> float:
    raise ValueError(f"{name} is not a JSON number")


def viewer_data(html: str) -> dict[str, Any] | None:
    """The ``const DATA = {...};`` object embedded in the web viewer, or ``None`` if the page has
    no such block. Raises ``DataError`` if the block is
    repeated, is not strict JSON, is not an object or is not closed by ``;``."""
    starts = [m.end() for m in VIEWER_BLOCK.finditer(html)]
    if not starts:
        return None
    if len(starts) > 1:
        raise DataError(f"{VIEWER_FILE}: `const DATA` appears {len(starts)} times")
    decoder = json.JSONDecoder(parse_constant=_reject_constant)
    try:
        value, end = decoder.raw_decode(html, starts[0])
    except (json.JSONDecodeError, ValueError) as error:
        raise DataError(f"{VIEWER_FILE}: `const DATA` is not strict JSON ({error})") from error
    if not isinstance(value, dict):
        raise DataError(f"{VIEWER_FILE}: `const DATA` is not an object")
    if not html[end:].lstrip().startswith(";"):
        raise DataError(f"{VIEWER_FILE}: `const DATA` must end with `;`")
    return value


def _same_number(shown: object, published: object) -> bool:
    numbers = (int, float)
    if isinstance(shown, bool) or isinstance(published, bool):
        return type(shown) is type(published) and shown == published
    if isinstance(shown, numbers) and isinstance(published, numbers):
        return float(shown) == float(published)
    return type(shown) is type(published) and shown == published


def _subset(shown: object, published: object, where: str,
            source: str = METRICS_FILE) -> list[str]:
    """Every value in ``shown`` must exist in ``published`` at the same place, equal."""
    if isinstance(shown, dict):
        if not isinstance(published, dict):
            return [f"{where}: is an object, {source} has {type(published).__name__}"]
        problems: list[str] = []
        for key, value in shown.items():
            if key not in published:
                problems.append(f"{where}.{key}: not in {source}")
            else:
                problems += _subset(value, published[key], f"{where}.{key}", source)
        return problems
    if isinstance(shown, list):
        if not isinstance(published, list) or len(shown) != len(published):
            return [f"{where}: list differs from {source}"]
        return [p for i, (a, b) in enumerate(zip(shown, published, strict=True))
                for p in _subset(a, b, f"{where}[{i}]", source)]
    if not _same_number(shown, published):
        return [f"{where}: {shown!r} but {source} has {published!r}"]
    return []


def compare_viewer(data: dict[str, Any], metrics_data: dict[str, Any],
                   table: dict[str, dict[int, float]]) -> list[str]:
    """Figures of the viewer's ``DATA`` block that do not match the published files (contract
    with the viewer: ``headline``, ``m1`` and ``m2`` are exact subsets of ``metricas.json``;
    ``by_municipality`` repeats ``superficies.csv`` in study order)."""
    problems: list[str] = []
    for key, source in VIEWER_KEYS.items():
        shown = data.get(key)
        if not isinstance(shown, dict) or not shown:
            problems.append(f"{VIEWER_FILE}: DATA.{key} missing or empty")
            continue
        problems += _subset(shown, metrics_data[source], f"{VIEWER_FILE}: DATA.{key}")
    rows = data.get("by_municipality")
    names = list(MUNICIPALITIES.values())
    if not isinstance(rows, list) or len(rows) != len(names):
        problems.append(f"{VIEWER_FILE}: DATA.by_municipality must list {len(names)} "
                        f"municipalities in study order")
        return problems
    for i, (row, name) in enumerate(zip(rows, names, strict=True)):
        where = f"{VIEWER_FILE}: DATA.by_municipality[{i}]"
        if not isinstance(row, dict) or row.get("name") != name:
            shown_name = row.get("name") if isinstance(row, dict) else row
            problems.append(f"{where}: {shown_name!r} where {name!r} was expected")
            continue
        hectares = row.get("ha")
        expected = table.get(name, {})
        if not isinstance(hectares, dict):
            problems.append(f"{where}.ha: missing")
            continue
        codes = {str(code) for code in expected}
        if set(hectares) != codes:
            problems.append(f"{where}.ha: classes {sorted(hectares)} but {AREAS_FILE} has "
                            f"{sorted(codes, key=int)}")
            continue
        for code, value in expected.items():
            if not _same_number(hectares[str(code)], value):
                problems.append(f"{where}.ha[{code}]: {hectares[str(code)]!r} but {AREAS_FILE} "
                                f"has {value}")
    return problems


def adjustment_figures(adjustment: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    """(hectares, percentages) of the calibrated headline that every document must show:
    hectares and their interval; validation accuracy, commission, omission and area difference
    of the chosen index, and its share of EMSN188 damage points in burned."""
    chosen = adjustment["chosen"]
    v = adjustment["indices"][chosen["index"]]["validation"]
    hectares = [chosen["hectares"], *chosen["hectares_interval"]]
    rates = [v["accuracy_pct"], v["commission_pct"], v["omission_pct"],
             v["area_difference_pct"], adjustment["emsn188"][chosen["index"]]["pct"]]
    return hectares, rates


def compare_adjustment(root: Path, adjustment: Mapping[str, Any],
                       viewer: Mapping[str, Any] | None) -> list[str]:
    """Calibrated headline figures missing from the README files or not matching in the
    viewer's ``DATA.calibrated`` block (an exact subset of ``ajuste_umbrales.json``)."""
    hectares, rates = adjustment_figures(adjustment)
    problems: list[str] = []
    for name, spanish in (("README.md", True), ("README.en.md", False)):
        text = (Path(root) / name).read_text(encoding="utf-8")
        for value in hectares:
            if _number(value, spanish) not in text:
                problems.append(f"{name}: {_number(value, spanish)} ha ({ADJUSTMENT_FILE}) "
                                "not found")
        for value in rates:
            shown = f"{value}".replace(".", ",") if spanish else f"{value}"
            if not re.search(rf"(?<![\d.,]){re.escape(shown)} %", text):
                problems.append(f"{name}: {shown} % ({ADJUSTMENT_FILE}) not found")
    if viewer is not None:
        problems += compare_viewer_adjustment(viewer, adjustment)
    return problems


def compare_viewer_adjustment(viewer: Mapping[str, Any],
                              adjustment: Mapping[str, Any]) -> list[str]:
    """``DATA.calibrated`` of the viewer must exist and be an exact subset of
    ``ajuste_umbrales.json``, and must carry the calibrated headline."""
    block = viewer.get(VIEWER_ADJUSTMENT_KEY)
    where = f"{VIEWER_FILE}: DATA.{VIEWER_ADJUSTMENT_KEY}"
    if not isinstance(block, dict) or not block:
        return [f"{where} missing or empty"]
    problems = _subset(block, adjustment, where, ADJUSTMENT_FILE)
    chosen = block.get("chosen")
    if not isinstance(chosen, dict) or "hectares" not in chosen:
        problems.append(f"{where}.chosen.hectares missing: the headline cannot be checked")
    return problems


def compare_documents(root: Path) -> list[str]:
    """Figures in ``README.md`` (Spanish), ``README.en.md`` (English) and ``docs/index.html``
    that do not match ``datos/metricas.json``, ``datos/superficies.csv`` and, when published,
    ``datos/ajuste_umbrales.json`` (empty if every figure matches)."""
    root = Path(root)
    data = root / "datos"
    m: dict[str, Any] = json.loads((data / METRICS_FILE).read_text(encoding="utf-8"))
    table = read_areas(data / AREAS_FILE)
    m1, m2 = m["measurement_1"], m["measurement_2"]
    rates = [m2["B"]["weighted_accuracy_pct"], m2["A"]["weighted_accuracy_pct"],
             m2["B"]["commission_pct"], m2["A"]["commission_pct"],
             m2["B"]["weighted_omission_pct"], m2["A"]["weighted_omission_pct"],
             m1["A"]["pct"], m1["B"]["pct"]]
    problems: list[str] = []
    for name, spanish in (("README.md", True), ("README.en.md", False)):
        text = (root / name).read_text(encoding="utf-8")
        totals = [0.0] * 6
        for municipality, areas in table.items():
            values = [areas[4], areas[5], areas[6], areas[7], areas[5] + areas[6] + areas[7],
                      areas[4] + areas[5] + areas[6] + areas[7]]
            totals = [t + v for t, v in zip(totals, values, strict=True)]
            line = f"| {municipality} | " + " | ".join(_number(v, spanish) for v in values) + " |"
            if line not in text:
                problems.append(f"{name}: row of {municipality} does not match {AREAS_FILE}")
        total = "| **Total** | " + " | ".join(f"**{_number(v, spanish)}**" for v in totals) + " |"
        if total not in text:
            problems.append(f"{name}: total row does not match {AREAS_FILE}")
        for value in rates:
            shown = f"{value}".replace(".", ",") if spanish else f"{value}"
            if not re.search(rf"(?<![\d.,]){re.escape(shown)} %", text):
                problems.append(f"{name}: {shown} % not found")
    html = (root / VIEWER_FILE).read_text(encoding="utf-8")
    viewer = viewer_data(html)
    if viewer is None:
        problems.append(f"{VIEWER_FILE}: no `const DATA` block, its figures cannot be checked")
    else:
        problems += compare_viewer(viewer, m, table)
    adjustment_path = data / ADJUSTMENT_FILE
    if adjustment_path.exists():
        adjustment = json.loads(adjustment_path.read_text(encoding="utf-8"))
        problems += compare_adjustment(root, adjustment, viewer)
    return problems


def read_classes(path: Path) -> tuple[np.ndarray, Affine, str]:
    """Class raster, its transform and CRS."""
    with rasterio.open(path) as r:
        return r.read(1), r.transform, r.crs.to_string()
