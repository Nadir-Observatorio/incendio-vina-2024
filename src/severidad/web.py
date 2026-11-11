"""Severity classes as a transparent PNG for a web map (MapLibre image source).

Web maps draw in Web Mercator (EPSG:3857). An image source is stretched between four lon/lat
corners, so the raster is first warped to Web Mercator with nearest-neighbour resampling (classes
are categories, never averaged); its corners are then exact and the image lines up with the base
map at every zoom level.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.warp import Resampling, calculate_default_transform, reproject

from .errors import DataError
from .estudio import PALETTE

WEB_CRS = "EPSG:3857"
# RGBA per class code (``estudio.PALETTE``): "unburned" and "no data" are transparent so the base
# map shows.


def to_web_mercator(classes: np.ndarray, transform: Affine,
                    crs: str) -> tuple[np.ndarray, Affine]:
    """Classes warped to EPSG:3857 (nearest neighbour; 0 outside the source)."""
    height, width = classes.shape
    left, top = transform * (0, 0)
    right, bottom = transform * (width, height)
    dst_transform, dst_w, dst_h = calculate_default_transform(
        crs, WEB_CRS, width, height, left=left, bottom=bottom, right=right, top=top)
    out = np.zeros((dst_h, dst_w), dtype=np.uint8)
    reproject(classes, out, src_transform=transform, src_crs=crs, dst_transform=dst_transform,
              dst_crs=WEB_CRS, resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
    return out, dst_transform


def rgba(classes: np.ndarray) -> np.ndarray:
    """RGBA image (height, width, 4) of the class codes; unknown codes raise."""
    unknown = set(np.unique(classes).tolist()) - set(PALETTE)
    if unknown:
        raise DataError(f"class codes without colour: {sorted(unknown)}")
    lut = np.zeros((256, 4), dtype=np.uint8)
    for code, colour in PALETTE.items():
        lut[code] = colour
    image: np.ndarray = lut[classes]
    return image


def corners(transform: Affine, shape: tuple[int, int]) -> list[list[float]]:
    """Lon/lat of the image corners in MapLibre order: top-left, top-right, bottom-right,
    bottom-left."""
    height, width = shape
    to_lonlat = Transformer.from_crs(WEB_CRS, "EPSG:4326", always_xy=True).transform
    pixels = ((0, 0), (width, 0), (width, height), (0, height))
    return [[round(v, 6) for v in to_lonlat(*(transform * p))] for p in pixels]


def write_png(classes_tif: Path, png: Path) -> list[list[float]]:
    """Write the web PNG of a classes GeoTIFF and return its corners. Refuses to overwrite."""
    import matplotlib.image

    png = Path(png)
    if png.exists():
        raise FileExistsError(png)
    with rasterio.open(classes_tif) as r:
        classes, transform, crs = r.read(1), r.transform, r.crs.to_string()
    web, web_transform = to_web_mercator(classes, transform, crs)
    matplotlib.image.imsave(png, rgba(web), format="png")
    return corners(web_transform, web.shape)
