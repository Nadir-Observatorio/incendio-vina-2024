"""Tests of `severidad.calculo` on a small synthetic tile (no download needed)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from severidad import calculo

SIDE = 200
X0, Y0 = 260000.0, 6345000.0
BURN = (slice(70, 130), slice(20, 80))      # 60 x 60 px = 144 ha
WATER = (slice(0, 10), slice(None))


def _tif(path: Path, data: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=SIDE, height=SIDE, count=1,
                       dtype=data.dtype.name, crs="EPSG:32719",
                       transform=from_origin(X0, Y0, 20, 20), nodata=0) as r:
        r.write(data, 1)
    return path


def _scene(folder: Path, nir: np.ndarray, swir: np.ndarray, scl: np.ndarray,
           with_offset: bool) -> dict[str, Path]:
    add = 0.1 if with_offset else 0.0
    dn = lambda refl: np.round((refl + add) / 0.0001).clip(1, 65535).astype(np.uint16)  # noqa: E731
    return {"nir08": _tif(folder / "nir08.tif", dn(nir)),
            "swir22": _tif(folder / "swir22.tif", dn(swir)),
            "scl": _tif(folder / "scl.tif", scl)}


def _pair(tmp: Path, with_offset: bool = False) -> tuple[dict, dict, tuple]:
    rng = np.random.default_rng(0)
    noise = lambda: rng.normal(0, 0.005, (SIDE, SIDE))  # noqa: E731
    nir_a, swir_a = 0.30 + noise(), 0.15 + noise()
    nir_b, swir_b = 0.31 + noise(), 0.15 + noise()      # +0.01 NIR bias between dates
    nir_b[BURN], swir_b[BURN] = 0.12, 0.28
    for band in (nir_a, swir_a, nir_b, swir_b):
        band[WATER] = 0.01
    scl = np.full((SIDE, SIDE), 4, dtype=np.uint8)
    scl[WATER] = 6
    scl_b = scl.copy()
    scl_b[BURN] = 2                                     # scar labelled "dark"
    pre = _scene(tmp / "pre", nir_a, swir_a, scl, with_offset)
    post = _scene(tmp / "post", nir_b, swir_b, scl_b, with_offset)
    t = Transformer.from_crs("EPSG:32719", "EPSG:4326", always_xy=True)
    w, n = t.transform(X0 + 1, Y0 - 1)
    e, s = t.transform(X0 + SIDE * 20 - 1, Y0 - SIDE * 20 + 1)
    return pre, post, (w, s, e, n)


@pytest.mark.parametrize("with_offset", [False, True])
def test_burned_square_found_with_or_without_data_offset(tmp_path: Path, with_offset: bool) -> None:
    """The 144 ha scar must be classified as burned whether or not the files carry +1000."""
    pre, post, bbox = _pair(tmp_path, with_offset)
    r = calculo.severity(pre, post, bbox)
    assert (r.classes[BURN] >= 4).all()
    outside = np.ones_like(r.classes, dtype=bool)
    outside[60:140, 10:90] = False
    outside[WATER] = False
    assert (r.classes[outside] == 3).mean() > 0.99


def test_offset_absorbs_bias(tmp_path: Path) -> None:
    """The ring offset removes the radiometric bias between dates."""
    pre, post, bbox = _pair(tmp_path)
    r = calculo.severity(pre, post, bbox)
    assert r.offset < -0.01 and abs(np.nanmedian(r.dnbr[r.classes == 3])) < 5.0


def test_water_is_no_data(tmp_path: Path) -> None:
    """Water pixels are excluded, never classified."""
    pre, post, bbox = _pair(tmp_path)
    assert (calculo.severity(pre, post, bbox).classes[WATER] == 0).all()


def test_content_hash_ignores_compression(tmp_path: Path) -> None:
    """Same pixels with different compression give the same content hash."""
    data = np.arange(64, dtype=np.uint8).reshape(8, 8)
    paths = []
    for c in ("deflate", "lzw"):
        p = tmp_path / f"{c}.tif"
        with rasterio.open(p, "w", driver="GTiff", width=8, height=8, count=1, dtype="uint8",
                           crs="EPSG:32719", transform=from_origin(0, 0, 20, 20),
                           compress=c) as r:
            r.write(data, 1)
        paths.append(p)
    assert paths[0].read_bytes() != paths[1].read_bytes()
    assert calculo.content_hash(paths[0]) == calculo.content_hash(paths[1])


def test_write_refuses_to_overwrite(tmp_path: Path) -> None:
    pre, post, bbox = _pair(tmp_path)
    r = calculo.severity(pre, post, bbox)
    calculo.write_classes(r, tmp_path / "c.tif")
    with pytest.raises(FileExistsError):
        calculo.write_classes(r, tmp_path / "c.tif")
