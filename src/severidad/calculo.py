"""Burn severity: window, reflectance, quality mask, dNBR with offset, classes and areas.

Every step is a published method; the parameters are module constants so a reader can see and
change them in one place.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds
from scipy.ndimage import distance_transform_edt

from .errors import DataError

logger = logging.getLogger(__name__)

SCALE = 0.0001              # Sentinel-2 L2A digital number to reflectance
WATER_SCL = 6
WATER_DN_THRESHOLD = 500    # water SWIR is ~0: median DN < 500 means no +1000 offset in the data
INVALID_PRE = frozenset({0, 1, 2, 3, 6, 8, 9, 10, 11})
INVALID_POST = frozenset({0, 1, 6, 8, 9, 10, 11})  # dark (2) and shadow (3) kept: burn scars
RING_THRESHOLD = 0.1        # preliminary burned area: raw dNBR >= 0.1
RING_INNER_M, RING_OUTER_M = 500.0, 2000.0          # unburned ring for the offset (Parks 2018)
# Key and Benson (2006), dNBR x 1000, half-open [from, to); code 0 = no data.
CLASSES = (
    (1, "Enhanced regrowth, high", -np.inf, -250.0),
    (2, "Enhanced regrowth, low", -250.0, -100.0),
    (3, "Unburned", -100.0, 100.0),
    (4, "Low severity", 100.0, 270.0),
    (5, "Moderate-low severity", 270.0, 440.0),
    (6, "Moderate-high severity", 440.0, 660.0),
    (7, "High severity", 660.0, np.inf),
)


@dataclass(frozen=True)
class Result:
    classes: np.ndarray        # uint8, 0 = no data
    dnbr: np.ndarray           # float64, x 1000, offset removed; NaN = no data
    transform: rasterio.Affine
    crs: str
    offset: float
    ring_pixels: int
    water_dn: tuple[float, float]


def window(path: Path, bbox: tuple[float, float, float, float]) -> Window:
    """Raster window covering a lon/lat bbox (west, south, east, north)."""
    with rasterio.open(path) as r:
        t = Transformer.from_crs("EPSG:4326", r.crs, always_xy=True)
        xs, ys = t.transform([bbox[0], bbox[2], bbox[0], bbox[2]],
                             [bbox[1], bbox[1], bbox[3], bbox[3]])
        w = from_bounds(min(xs), min(ys), max(xs), max(ys), r.transform)
        return w.round_offsets().round_lengths().intersection(Window(0, 0, r.width, r.height))


def _read(path: Path, w: Window) -> tuple[np.ndarray, rasterio.Affine, str]:
    with rasterio.open(path) as r:
        return r.read(1, window=w), r.window_transform(w), r.crs.to_string()


def _reflectance(scene: dict[str, Path],
                 w: Window) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """NIR and SWIR reflectance, SCL, and the water median DN used to decide the offset."""
    nir, t1, _ = _read(scene["nir08"], w)
    swir, t2, _ = _read(scene["swir22"], w)
    scl, t3, _ = _read(scene["scl"], w)
    if not (t1 == t2 == t3 and nir.shape == swir.shape == scl.shape):
        raise DataError("bands of one scene are not on the same grid")
    water = (scl == WATER_SCL) & (swir > 0)
    if water.sum() < 1000:
        raise DataError("fewer than 1000 water pixels: cannot measure the reflectance offset")
    water_dn = float(np.median(swir[water]))
    offset = 0.0 if water_dn < WATER_DN_THRESHOLD else -1000 * SCALE
    out = []
    for dn in (nir, swir):
        refl = dn.astype(np.float64) * SCALE + offset
        refl[dn == 0] = np.nan
        out.append(refl)
    return out[0], out[1], scl, water_dn


def _nbr(nir: np.ndarray, swir: np.ndarray) -> np.ndarray:
    total = nir + swir
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total != 0.0, (nir - swir) / total, np.nan).astype(np.float64)


def severity(pre: dict[str, Path], post: dict[str, Path],
             bbox: tuple[float, float, float, float]) -> Result:
    """Severity classes on the window of `bbox` (both scenes must share the tile grid)."""
    w = window(pre["nir08"], bbox)
    nir_a, swir_a, scl_a, wa = _reflectance(pre, w)
    nir_b, swir_b, scl_b, wb = _reflectance(post, w)
    _, transform, crs = _read(pre["scl"], w)
    _, transform_b, _ = _read(post["scl"], w)
    if transform != transform_b:
        raise DataError("pre and post scenes are not on the same grid")

    valid = ~np.isin(scl_a, list(INVALID_PRE)) & ~np.isin(scl_b, list(INVALID_POST))
    for band in (nir_a, swir_a, nir_b, swir_b):
        valid &= np.isfinite(band) & (band > 0.0)
    raw = _nbr(nir_a, swir_a) - _nbr(nir_b, swir_b)
    valid &= np.isfinite(raw)
    raw = np.where(valid, raw, np.nan)

    pixel = abs(transform.a)
    burned = valid & (raw >= RING_THRESHOLD)
    distance = distance_transform_edt(~burned)
    ring = valid & (distance > RING_INNER_M / pixel) & (distance <= RING_OUTER_M / pixel)
    offset = float(raw[ring].mean())
    logger.info("water DN %.0f / %.0f; offset %.4f over %d ring pixels", wa, wb, offset,
                int(ring.sum()))

    dnbr = (raw - offset) * 1000.0
    classes = np.zeros(dnbr.shape, dtype=np.uint8)
    usable = valid & np.isfinite(dnbr)
    for code, _name, low, high in CLASSES:
        classes[usable & (dnbr >= low) & (dnbr < high)] = code
    return Result(classes, dnbr, transform, crs, offset, int(ring.sum()), (wa, wb))


def write_classes(result: Result, path: Path) -> Path:
    """GeoTIFF of the classes (uint8, 0 = no data). Refuses to overwrite."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    profile = {"driver": "GTiff", "width": result.classes.shape[1],
               "height": result.classes.shape[0], "count": 1, "dtype": "uint8",
               "crs": result.crs, "transform": result.transform, "nodata": 0,
               "compress": "deflate", "tiled": True, "blockxsize": 256, "blockysize": 256}
    with rasterio.open(path, "w", **profile) as r:
        r.write(result.classes, 1)
    return path


def content_hash(path: Path) -> str:
    """sha256 of band 1 content (dtype, shape, CRS, transform and pixels), independent of the
    file compression: the same on any operating system."""
    with rasterio.open(path) as r:
        data = r.read(1)
        crs = r.crs.to_string() if r.crs else ""
        transform = tuple(round(float(v), 9) for v in tuple(r.transform)[:6])
    if np.issubdtype(data.dtype, np.floating):
        data = np.where(np.isnan(data), np.array(np.nan, dtype=data.dtype), data)
    data = np.ascontiguousarray(data.astype(data.dtype.newbyteorder("<")))
    h = hashlib.sha256()
    h.update(f"{data.dtype.str}|{data.shape}|{crs}|{transform}|".encode("ascii"))
    h.update(data.tobytes())
    return h.hexdigest()


def areas(result: Result, zones: dict[str, object]) -> dict[str, dict[int, float]]:
    """Hectares per zone and class. `zones` maps a name to a shapely geometry in the result CRS;
    a pixel belongs to the zone that contains its centre."""
    ha = abs(result.transform.a * result.transform.e) / 10_000.0
    out: dict[str, dict[int, float]] = {}
    for name, geometry in zones.items():
        inside = rasterize([(geometry, 1)], out_shape=result.classes.shape,
                           transform=result.transform, fill=0, dtype="uint8").astype(bool)
        out[name] = {code: float((inside & (result.classes == code)).sum()) * ha
                     for code in range(0, 8)}
    return out
