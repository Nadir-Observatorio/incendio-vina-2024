"""Context layers for the maps: relief, water, built-up area, city names and an outline of Chile.

Two steps, so that the published maps can be rebuilt from ``datos/`` alone:

    python -m severidad.contexto descargar   # sources to datos_crudos/contexto/ (needs internet)
    python -m severidad.contexto derivar     # small layers to datos/contexto/ (offline)

``descargar`` fetches the Copernicus DEM GLO-30 tiles and the ESA WorldCover 2021 v200 tiles that
cover the processing window, the Natural Earth 1:50m countries and the GeoNames extract of Chile.
Every file is written next to a ``.part`` first, its size is checked against ``Content-Length``,
and a row (URL, bytes, sha256, UTC time) is appended to ``datos_crudos/contexto/registro.csv``.
Existing files are never overwritten.

The WorldCover tiles cover 3 x 3 degrees at 10 m (36,000 x 36,000 pixels); the window crosses
33 degrees south and needs two of them.

``derivar`` writes, on the exact grid of ``datos/clases.tif``:

- ``relieve.tif``: hillshade (uint8, 1 to 255; 0 = no data) of the DEM resampled bilinearly;
- ``agua.tif``: 0 land, 1 sea, 2 inland water. Water is class 6 of the Sentinel-2 scene
  classification in both scenes; sea is the water connected to the edge of the window;
- ``construido.tif``: 1 where ESA WorldCover 2021 says "built-up" (class 50: buildings, roads and
  other structures), 0 elsewhere, resampled by nearest neighbour. It is drawn as a reference
  outline only; no figure of the study is computed with it. It shows 2021, so it misses later
  settlements and can under-represent scattered self-built housing;

and, in lon/lat, ``lugares.geojson`` (GeoNames populated places inside the window with at least
50,000 inhabitants, to label the cities) and ``chile.geojson`` (Chile and its neighbours, for the
locator). Municipal boundaries are deliberately not drawn nor redistributed: the official layer
asks for its derived products to be validated by DIFROL. ``procedencia.json`` lists the sha256 of
every input and output (content hash for the rasters, which is the same on Windows and Linux).
Existing outputs are never overwritten.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import sys
import urllib.request
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from pathlib import Path

import numpy as np

from .calculo import content_hash
from .errors import DataError, SourceError
from .estudio import BBOX, POST_SCENE, PRE_SCENE

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "datos_crudos"
SOURCES = RAW / "contexto"
OUT = ROOT / "datos" / "contexto"
CHUNK = 1 << 20

DEM_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
# ESA WorldCover 10 m 2021 v200 (doi:10.5281/zenodo.7254221), CC BY 4.0. COG tiles of 3 x 3 degrees
# named by their south-west corner, on a grid aligned to multiples of 3 degrees; URL pattern of the
# project's own download script (github.com/ESA-WorldCover/esa-worldcover-datasets).
WORLDCOVER_BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
WORLDCOVER_TILE_DEGREES = 3
WORLDCOVER_NO_DATA = 0
WORLDCOVER_BUILT = 50
NATURAL_EARTH = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"
NE_FILE = "ne_50m_admin_0_countries.zip"
GEONAMES = "https://download.geonames.org/export/dump/CL.zip"
GEONAMES_FILE = "CL.zip"
GEONAMES_COLUMNS = 19
MIN_POPULATION = 50_000        # cities labelled on the maps; fixed before looking at the result
NEIGHBOURS = ("CHL", "ARG", "BOL", "PER")
SCL_WATER = 6
LAND, SEA, INLAND = 0, 1, 2
ATTRIBUTION = {
    "dem": ("Relief produced using Copernicus WorldDEM-30 \u00a9 DLR e.V. 2010-2014 and \u00a9 "
            "Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European "
            "Union and ESA; all rights reserved"),
    "natural_earth": "Made with Natural Earth (public domain)",
    "geonames": "City names and positions: GeoNames (geonames.org), CC BY 4.0",
    "water": "Contains modified Copernicus Sentinel data 2024",
    "worldcover": ("\u00a9 ESA WorldCover project 2021 / Contains modified Copernicus Sentinel "
                   "data (2021) processed by ESA WorldCover consortium"),
}


def dem_tiles(bbox: tuple[float, float, float, float] = BBOX) -> list[str]:
    """Names of the 1 x 1 degree GLO-30 tiles that cover (west, south, east, north).

    Tiles are named by their south-west corner."""
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise DataError(f"empty bounding box: {bbox}")
    names = []
    for lat in range(math.floor(south), math.floor(north) + 1):
        for lon in range(math.floor(west), math.floor(east) + 1):
            ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
            names.append(f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM")
    return names


def dem_url(name: str) -> str:
    return f"{DEM_BASE}/{name}/{name}.tif"


def worldcover_tiles(bbox: tuple[float, float, float, float] = BBOX) -> list[str]:
    """Names (``S36W072``) of the 3 x 3 degree WorldCover tiles that cover (west, south, east,
    north), south to north and west to east. Tiles are named by their south-west corner."""
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise DataError(f"empty bounding box: {bbox}")
    step = WORLDCOVER_TILE_DEGREES
    names = []
    for lat in range(math.floor(south / step) * step, math.floor(north / step) * step + 1, step):
        for lon in range(math.floor(west / step) * step, math.floor(east / step) * step + 1, step):
            ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
            names.append(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")
    return names


def worldcover_file(tile: str) -> str:
    return f"ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"


def worldcover_url(tile: str) -> str:
    return f"{WORLDCOVER_BASE}/{worldcover_file(tile)}"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, path: Path, registry: Path, timeout: float = 600.0) -> Path:
    """Download ``url`` to ``path`` unless it exists; record it in ``registry`` (CSV).

    A partial download stays as ``.part`` and raises, so it is never read as complete."""
    path = Path(path)
    if path.exists():
        logger.info("%s already present (%s)", path.name, sha256(path)[:12])
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    if urlsplit(url).scheme not in ("https", "file"):   # file: only for the tests
        raise SourceError(f"refusing a non-https source: {url}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r, part.open("wb") as f:  # noqa: S310
            expected = r.headers.get("Content-Length")
            written = 0
            for block in iter(lambda: r.read(CHUNK), b""):
                f.write(block)
                written += len(block)
    except OSError as e:
        raise SourceError(f"{url}: {e}") from e
    if expected is not None and int(expected) != written:
        raise SourceError(f"{path.name}: received {written} of {expected} bytes; {part.name} kept")
    part.replace(path)
    digest = sha256(path)
    new = not registry.exists()
    with registry.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(["file", "url", "bytes", "sha256", "downloaded_utc"])
        w.writerow([path.name, url, written, digest,
                    datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")])
    logger.info("%s: %d bytes, sha256 %s", path.name, written, digest[:12])
    return path


def fetch(folder: Path = SOURCES) -> list[Path]:
    """Download the DEM and WorldCover tiles, Natural Earth countries and GeoNames to
    ``folder``."""
    registry = Path(folder) / "registro.csv"
    paths = [download(dem_url(n), Path(folder) / f"{n}.tif", registry) for n in dem_tiles()]
    paths += [download(worldcover_url(t), Path(folder) / worldcover_file(t), registry)
              for t in worldcover_tiles()]
    paths.append(download(NATURAL_EARTH, Path(folder) / NE_FILE, registry))
    paths.append(download(GEONAMES, Path(folder) / GEONAMES_FILE, registry))
    return paths


def hillshade(dem: np.ndarray, cell: float, azimuth: float = 315.0, altitude: float = 45.0,
              exaggeration: float = 1.5) -> np.ndarray:
    """Hillshade in [0, 1] of a north-up DEM with square cells of ``cell`` metres.

    NaN where the DEM or any neighbour used by the gradient is NaN."""
    z = dem.astype("float64") * exaggeration
    dzdy, dzdx = np.gradient(z, cell)          # rows grow southwards
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(-dzdx, dzdy)           # 0 = north, clockwise positive
    az, alt = np.radians(azimuth), np.radians(altitude)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    shade = np.clip(shade, 0.0, 1.0)
    # The central difference skips the pixel itself, so a hole would get a shade: keep it a hole.
    shade[~np.isfinite(z)] = np.nan
    return np.asarray(shade, dtype="float64")


def water_classes(scl_pre: np.ndarray, scl_post: np.ndarray) -> np.ndarray:
    """0 land, 1 sea, 2 inland water: water in both scenes; sea touches the window edge."""
    from scipy import ndimage

    if scl_pre.shape != scl_post.shape:
        raise DataError(f"scene classification grids differ: {scl_pre.shape} vs {scl_post.shape}")
    water = (scl_pre == SCL_WATER) & (scl_post == SCL_WATER)
    labels, _ = ndimage.label(water)
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    sea = np.isin(labels, np.unique(edge[edge > 0]))
    out = np.full(water.shape, LAND, dtype=np.uint8)
    out[water] = INLAND
    out[sea] = SEA
    return out


def _grid() -> tuple[dict[str, Any], Any]:
    import rasterio

    with rasterio.open(ROOT / "datos" / "clases.tif") as r:
        return dict(r.profile), r.bounds


def _scl_on_grid(scene: str, bounds: Any, shape: tuple[int, int]) -> np.ndarray:
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(RAW / scene / "scl.tif") as r:
        window = from_bounds(*bounds, transform=r.transform)
        data = r.read(1, window=window, boundless=True, fill_value=0)
    if data.shape != shape:
        raise DataError(f"{scene} scl does not align with clases.tif: {data.shape} vs {shape}")
    return np.asarray(data)


def _relief(profile: dict[str, Any]) -> np.ndarray:
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import Resampling, reproject

    tiles = [SOURCES / f"{n}.tif" for n in dem_tiles()]
    missing = [t.name for t in tiles if not t.exists()]
    if missing:
        raise DataError(f"DEM tiles missing (run 'descargar'): {missing}")
    sources = [rasterio.open(t) for t in tiles]
    try:
        mosaic, transform = merge(sources)
        src_crs, nodata = sources[0].crs, sources[0].nodata
    finally:
        for s in sources:
            s.close()
    dem = np.full((profile["height"], profile["width"]), np.nan, dtype="float32")
    reproject(mosaic[0].astype("float32"), dem, src_transform=transform, src_crs=src_crs,
              src_nodata=nodata, dst_transform=profile["transform"], dst_crs=profile["crs"],
              dst_nodata=np.nan, resampling=Resampling.bilinear)
    shade = hillshade(dem, abs(profile["transform"].a))
    out = np.zeros(dem.shape, dtype=np.uint8)
    ok = np.isfinite(shade)
    out[ok] = (1 + np.round(shade[ok] * 254)).astype(np.uint8)
    return out


def built_mask(landcover: np.ndarray) -> np.ndarray:
    """1 where a WorldCover class grid is built-up (class 50), else 0 (uint8)."""
    mask: np.ndarray = (np.asarray(landcover) == WORLDCOVER_BUILT).astype(np.uint8)
    return mask


def built_on_grid(tiles: list[Path], profile: dict[str, Any]) -> tuple[np.ndarray, int]:
    """Built-up mask of the WorldCover ``tiles`` on the grid of ``profile`` and the number of
    grid pixels that no tile covered with data (WorldCover no data or outside every tile).

    Each tile is warped straight onto the grid by nearest neighbour (classes are categories,
    never averaged); GDAL reads only the blocks it needs, so the 3 x 3 degree tiles (36,000 x
    36,000 pixels) are never loaded whole. A later tile does not erase what an earlier one wrote."""
    import rasterio
    from rasterio.warp import Resampling, reproject

    missing = [Path(t).name for t in tiles if not Path(t).exists()]
    if missing:
        raise DataError(f"WorldCover tiles missing (run 'descargar'): {missing}")
    landcover = np.full((profile["height"], profile["width"]), WORLDCOVER_NO_DATA, dtype=np.uint8)
    for tile in tiles:
        with rasterio.open(tile) as src:
            if src.count != 1 or src.dtypes[0] != "uint8":
                raise DataError(f"{Path(tile).name}: expected one uint8 band of classes")
            reproject(rasterio.band(src, 1), landcover, src_nodata=WORLDCOVER_NO_DATA,
                      dst_transform=profile["transform"], dst_crs=profile["crs"],
                      dst_nodata=WORLDCOVER_NO_DATA, init_dest_nodata=False,
                      resampling=Resampling.nearest)
    gaps = int(np.count_nonzero(landcover == WORLDCOVER_NO_DATA))
    return built_mask(landcover), gaps


def _write_raster(path: Path, data: np.ndarray, profile: dict[str, Any]) -> None:
    import rasterio

    p = profile.copy()
    p.update(dtype="uint8", count=1, nodata=None, compress="deflate", predictor=2)
    with rasterio.open(path, "w", **p) as r:
        r.write(data, 1)


def countries(src: Path) -> dict[str, Any]:
    """Chile and its neighbours from a Natural Earth countries file (zip or any OGR file), as a
    GeoJSON FeatureCollection in lon/lat, simplified for a small locator map.

    Reads with ``pyogrio.raw`` (numpy only): geopandas is not a dependency of this repository."""
    from pyogrio.raw import read
    from shapely import from_wkb
    from shapely.geometry import mapping

    src = Path(src)
    if not src.exists():
        raise DataError(f"{src.name} missing (run 'descargar')")
    path = f"/vsizip/{src.as_posix()}" if src.suffix.lower() == ".zip" else str(src)
    meta, _fids, geometry, fields = read(path, columns=["ADM0_A3", "NAME"])
    names = list(meta["fields"])
    codes, labels = fields[names.index("ADM0_A3")], fields[names.index("NAME")]
    crs = (meta.get("crs") or "").upper()
    if crs and crs not in ("EPSG:4326", "OGC:CRS84"):
        raise DataError(f"{src.name}: expected lon/lat (EPSG:4326), found {crs}")
    feats: list[dict[str, Any]] = []
    for code, label, wkb in zip(codes, labels, geometry, strict=True):
        if code in NEIGHBOURS:
            g = from_wkb(wkb).simplify(0.02, preserve_topology=True)
            props = {"ADM0_A3": str(code), "NAME": str(label)}
            feats.append({"type": "Feature", "properties": props, "geometry": mapping(g)})
    found = {f["properties"]["ADM0_A3"] for f in feats}
    if found != set(NEIGHBOURS):
        raise DataError(f"{src.name} lacks {sorted(set(NEIGHBOURS) - found)}")
    return {"type": "FeatureCollection", "features": feats}


def places(src: Path, bbox: tuple[float, float, float, float],
           min_population: int = MIN_POPULATION) -> dict[str, Any]:
    """GeoNames populated places (feature class P) inside ``bbox`` (west, south, east, north) with
    at least ``min_population`` inhabitants, as GeoJSON points in lon/lat, to label the cities.

    ``src`` is the GeoNames country extract (``CL.zip`` or its ``CL.txt``): tab-separated UTF-8,
    columns as in the GeoNames readme (name 1, latitude 4, longitude 5, feature class 6,
    population 14). The population rule is fixed in advance, not chosen by looking at the map."""
    import zipfile

    src = Path(src)
    if not src.exists():
        raise DataError(f"{src.name} missing (run 'descargar')")
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            text = z.read(f"{src.stem}.txt").decode("utf-8")
    else:
        text = src.read_text(encoding="utf-8")
    west, south, east, north = bbox
    feats: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        cols = line.split("\t")
        if len(cols) != GEONAMES_COLUMNS:
            raise DataError(f"{src.name}: line {line_no} has {len(cols)} columns, expected "
                            f"{GEONAMES_COLUMNS}")
        if cols[6] != "P" or not cols[14] or int(cols[14]) < min_population:
            continue
        lat, lon = float(cols[4]), float(cols[5])
        if west <= lon <= east and south <= lat <= north:
            feats.append({"type": "Feature",
                          "properties": {"NAME": cols[1], "POPULATION": int(cols[14]),
                                         "GEONAMEID": int(cols[0])},
                          "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    feats.sort(key=lambda f: f["properties"]["NAME"])
    logger.info("populated places in the window (>= %d inhabitants): %s", min_population,
                ", ".join(f["properties"]["NAME"] for f in feats) or "none")
    return {"type": "FeatureCollection", "features": feats}


def derive(out: Path = OUT) -> list[Path]:
    """Write the context layers to ``out``; refuses if any output already exists."""
    out = Path(out)
    names = ("relieve.tif", "agua.tif", "construido.tif", "lugares.geojson", "chile.geojson",
             "procedencia.json")
    existing = [n for n in names if (out / n).exists()]
    if existing:
        raise FileExistsError(f"{out}: {existing} already exist; remove them to rebuild")
    profile, bounds = _grid()
    shape = (profile["height"], profile["width"])
    relief = _relief(profile)
    water = water_classes(_scl_on_grid(PRE_SCENE, bounds, shape),
                          _scl_on_grid(POST_SCENE, bounds, shape))
    worldcover = [SOURCES / worldcover_file(t) for t in worldcover_tiles()]
    built, gaps = built_on_grid(worldcover, profile)
    if gaps:
        logger.warning("WorldCover has no data for %d pixels of the grid (drawn as not built-up)",
                       gaps)
    cities = places(SOURCES / GEONAMES_FILE, BBOX)
    neighbours = countries(SOURCES / NE_FILE)
    out.mkdir(parents=True, exist_ok=True)
    _write_raster(out / "relieve.tif", relief, profile)
    _write_raster(out / "agua.tif", water, profile)
    _write_raster(out / "construido.tif", built, profile)
    for name, data in (("lugares.geojson", cities), ("chile.geojson", neighbours)):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                encoding="utf-8")
    inputs = [RAW / PRE_SCENE / "scl.tif", RAW / POST_SCENE / "scl.tif", SOURCES / NE_FILE,
              SOURCES / GEONAMES_FILE, *(SOURCES / f"{n}.tif" for n in dem_tiles()), *worldcover]
    record = {
        "note": "Context layers for the maps, derived by severidad.contexto. Not part of the "
                "severity result; they only help to read it.",
        "attribution": ATTRIBUTION,
        "hashes": "sha256 of the file bytes, except datos/clases.tif and the rasters written here: "
                  "content hash "
                  "(severidad.calculo.content_hash; dtype, shape, CRS, transform and pixels), "
                  "because GeoTIFF compression gives different bytes on Windows and Linux",
        "inputs": {"datos/clases.tif": content_hash(ROOT / "datos" / "clases.tif"),
                   **{p.relative_to(ROOT).as_posix(): sha256(p) for p in inputs}},
        "places": [f["properties"]["NAME"] for f in cities["features"]],
        "outputs": {n: (content_hash(out / n) if n.endswith(".tif") else sha256(out / n))
                    for n in names[:-1]},
        "water_classes": {"0": "land", "1": "sea", "2": "inland water"},
        "built_up": {"0": "not built-up", "1": "built-up (ESA WorldCover 2021 v200, class 50)",
                     "resampling": "nearest neighbour from 10 m to the grid of datos/clases.tif",
                     "pixels_without_worldcover_data": gaps,
                     "use": "reference outline on the maps only; no figure is computed with it"},
        "relief": "hillshade, azimuth 315, altitude 45, vertical exaggeration 1.5; 0 = no data",
    }
    (out / "procedencia.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    logger.info("written %s", ", ".join(names))
    return [out / n for n in names]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("descargar", "derivar"))
    args = parser.parse_args(argv)
    try:
        fetch() if args.step == "descargar" else derive()
    except (SourceError, DataError, FileExistsError) as e:
        logger.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
