"""Web viewer `docs/index.html`, built only from `datos/`, `severidad.estudio` and the two scenes.

    python visor/make_viewer.py            # refuses to overwrite
    python visor/make_viewer.py --force    # replaces the page and its layers
    python visor/make_viewer.py --force --solo-pagina    # replaces only docs/index.html

Writes, next to `docs/clases.png` (written by `severidad.web`):

- `docs/index.html`: the page, from `visor/plantilla.html`. It embeds two blocks that other code
  reads: `const CLASSES` (legend, checked by `tests/test_estudio.py`) and `const DATA` (every
  figure the page shows, checked by `python -m severidad verify`). The page
  formats the numbers in the browser (Spanish or English); hectares per class are summed there
  from `DATA.by_municipality`, so no figure on the page escapes `verify`;
- `docs/capas/construido.png`: outline of the 2021 built-up area (ESA WorldCover class 50, from
  `datos/contexto/construido.tif`);
- `docs/capas/antes.jpg` and `docs/capas/despues.jpg`: true colour of the two study scenes
  (Sentinel-2 L2A `visual` asset, downloaded with `python -m severidad download`).

Every layer is warped to Web Mercator on exactly the grid of `docs/clases.png`, so the four images
share the same corners and line up at every zoom level. MapLibre GL JS is served from
`docs/vendor/maplibre-gl/` (no CDN); its files are pinned by sha256 and their subresource
integrity hashes are written into the page.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from severidad import estudio, web  # noqa: E402

logger = logging.getLogger("viewer")

DATA_DIR = ROOT / "datos"
CONTEXT = DATA_DIR / "contexto"
RAW = ROOT / "datos_crudos"
DOCS = ROOT / "docs"
TEMPLATE = Path(__file__).resolve().parent / "plantilla.html"
VENDOR = DOCS / "vendor" / "maplibre-gl"
# MapLibre GL JS 5.24.0, the same bytes the page used to load from unpkg (same SRI hashes).
MAPLIBRE = {
    "maplibre-gl.js": "45a9b07a9189ce56054c620a947ccf41e291e58c95e9b61533b740aaa65ee5cb",
    "maplibre-gl.css": "ab1e70d59ec40465bae7e7030da2f3ccf28133fd502e62bd598eefbadfd7a732",
    "LICENSE.txt": "ee5fc05a0677eaf69601d2c7db0d9ecd6cc27c3abc1d0733bc9ed34707cf8ef2",
}
LAYERS = {"built": "capas/construido.png", "before": "capas/antes.jpg",
          "after": "capas/despues.jpg"}
BUILT_RGBA = (61, 69, 76, 235)     # same grey as the factsheet outline
JPEG_QUALITY = 85
LEGEND_ORDER = (7, 6, 5, 4, 3, 2, 1)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sri(path: Path) -> str:
    """Subresource integrity value (sha384, base64) of a file."""
    return "sha384-" + base64.b64encode(hashlib.sha384(Path(path).read_bytes()).digest()).decode()


def check_vendor(folder: Path = VENDOR) -> None:
    """The vendored MapLibre files must be the pinned ones."""
    for name, digest in MAPLIBRE.items():
        path = Path(folder) / name
        if not path.exists():
            raise FileNotFoundError(f"{path} missing: copy MapLibre GL JS 5.24.0 there")
        if sha256(path) != digest:
            raise ValueError(f"{path.name} is not the pinned MapLibre 5.24.0 file")


def legend_block() -> str:
    """`const CLASSES` in the form `tests/test_estudio.py` reads: names and colours of
    `severidad.estudio`, high severity first; transparent classes as `null`."""
    rows = []
    for code in LEGEND_ORDER:
        name = estudio.CLASS_NAMES[code]
        r, g, b, alpha = estudio.PALETTE[code]
        rows.append(f'    ["{name} (transparent)", null]' if alpha == 0
                    else f'    ["{name}", "rgb({r},{g},{b})"]')
    return "const CLASSES = [\n" + ",\n".join(rows) + "\n  ];"


def mercator_grid() -> tuple[dict[str, Any], list[list[float]]]:
    """Grid of `docs/clases.png` (EPSG:3857) and its corners, computed as `web.write_png` does."""
    with rasterio.open(DATA_DIR / "clases.tif") as r:
        classes, transform, crs = r.read(1), r.transform, r.crs.to_string()
    warped, web_transform = web.to_web_mercator(classes, transform, crs)
    grid = {"height": warped.shape[0], "width": warped.shape[1], "transform": web_transform,
            "crs": web.WEB_CRS}
    return grid, web.corners(web_transform, warped.shape)


def built_outline(grid: dict[str, Any]) -> np.ndarray:
    """RGBA outline (one pixel wide, inside the area) of the built-up mask on the web grid."""
    with rasterio.open(CONTEXT / "construido.tif") as r:
        mask, transform, crs = r.read(1), r.transform, r.crs.to_string()
    warped, web_transform = web.to_web_mercator(mask, transform, crs)
    if warped.shape != (grid["height"], grid["width"]) or web_transform != grid["transform"]:
        raise ValueError("construido.tif is not on the grid of clases.tif")
    built = warped == 1
    edge = built & ~ndimage.binary_erosion(built)
    out = np.zeros((*built.shape, 4), dtype=np.uint8)
    out[edge] = BUILT_RGBA
    return out


def true_colour(scene: str, grid: dict[str, Any]) -> np.ndarray:
    """RGB of a scene's `visual` asset on the web grid (average of the 10 m pixels; 0 = none)."""
    path = RAW / scene / "visual.tif"
    if not path.exists():
        raise FileNotFoundError(f"{path.name} of {scene} missing in {path.parent.parent}: run "
                                "'python -m severidad download --bands visual'")
    out = np.zeros((3, grid["height"], grid["width"]), dtype=np.uint8)
    with rasterio.open(path) as src:
        if src.count != 3 or set(src.dtypes) != {"uint8"}:
            raise ValueError(f"{path.name} of {scene}: expected 3 uint8 bands (visual asset)")
        reproject(rasterio.band(src, (1, 2, 3)), out, src_nodata=0, dst_nodata=0,
                  dst_transform=grid["transform"], dst_crs=grid["crs"],
                  resampling=Resampling.average)
    if not out.any(axis=0).all():
        empty = int((~out.any(axis=0)).sum())
        raise ValueError(f"{scene}: {empty} pixels of the window have no true colour")
    return np.moveaxis(out, 0, -1)


def viewer_data(corners: list[list[float]]) -> dict[str, Any]:
    """The `const DATA` object: the contract keys copied from `datos/`, plus what the page needs
    to draw (names, colours, layers). Raises if `estudio.compare_viewer` finds a difference."""
    metrics = json.loads((DATA_DIR / estudio.METRICS_FILE).read_text(encoding="utf-8"))
    table = estudio.read_areas(DATA_DIR / estudio.AREAS_FILE)
    places = json.loads((CONTEXT / "lugares.geojson").read_text(encoding="utf-8"))
    data: dict[str, Any] = {
        "headline": metrics["headline"],
        "m1": metrics["measurement_1"],
        "m2": metrics["measurement_2"],
        "by_municipality": [{"name": name, "ha": {str(c): v for c, v in table[name].items()}}
                            for name in estudio.MUNICIPALITIES.values()],
        "codes": list(LEGEND_ORDER),
        "names": {str(c): n for c, n in estudio.CLASS_NAMES.items()},
        "palette": {str(c): list(v) for c, v in estudio.PALETTE.items()},
        "thresholds": dict(estudio.THRESHOLDS),
        "seed": estudio.SEED,
        "sample_size": estudio.SAMPLE_SIZE,
        "scenes": {"before": estudio.PRE_SCENE, "after": estudio.POST_SCENE},
        "corners": corners,
        "layers": {"classes": "clases.png", **LAYERS},
        "places": [{"name": f["properties"]["NAME"], "lonlat": f["geometry"]["coordinates"]}
                   for f in places["features"]],
    }
    adjustment = json.loads((DATA_DIR / estudio.ADJUSTMENT_FILE).read_text(encoding="utf-8"))
    data[estudio.VIEWER_ADJUSTMENT_KEY] = calibrated_block(adjustment)
    problems = estudio.compare_viewer(data, metrics, table)
    problems += estudio.compare_viewer_adjustment(data, adjustment)
    if problems:
        raise ValueError("DATA does not match datos/: " + "; ".join(problems))
    # The legend sums the municipalities in the browser: it must give the published headline.
    burned_b = sum(v for row in table.values() for c, v in row.items()
                   if c >= estudio.THRESHOLDS[metrics["headline"]["threshold"]])
    if round(burned_b, 1) != metrics["headline"]["hectares"]:
        raise ValueError(f"superficies.csv sums {burned_b:.2f} ha, headline says "
                         f"{metrics['headline']['hectares']}")
    return data


def calibrated_block(adj: dict[str, Any]) -> dict[str, Any]:
    """What the page shows of the local calibration (an exact subset of ajuste_umbrales.json):
    the calibrated headline, its validation, the original thresholds on the same validation
    blocks and the EMSN188 check."""
    index = adj["chosen"]["index"]
    v = adj["indices"][index]["validation"]
    keep = ("accuracy_pct", "commission_pct", "omission_pct", "area_difference_pct")
    return {
        "date": adj["date"],
        "chosen": {k: adj["chosen"][k] for k in ("index", "threshold", "threshold_interval",
                                                  "hectares", "hectares_interval")},
        "indices": {index: {"validation": {k: v[k] for k in (
            *keep, "accuracy_interval", "commission_interval", "omission_interval",
            "area_difference_interval")}}},
        "published_thresholds_on_validation": {
            key: {k: adj["published_thresholds_on_validation"][key][k] for k in keep}
            for key in estudio.THRESHOLDS},
        "emsn188": {"sample": adj["emsn188"]["sample"],
                    **{key: {k: adj["emsn188"][key][k] for k in ("pct", "wilson")}
                       for key in (index, "A")}},
    }


def render(data: dict[str, Any], template: Path = TEMPLATE) -> str:
    html = template.read_text(encoding="utf-8")
    block = "const DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"
    for marker, value in (("/*CLASSES*/", legend_block()), ("/*DATA*/", block),
                          ("@SRI_JS@", sri(VENDOR / "maplibre-gl.js")),
                          ("@SRI_CSS@", sri(VENDOR / "maplibre-gl.css"))):
        if html.count(marker) != 1:
            raise ValueError(f"{template.name}: marker {marker} must appear exactly once")
        html = html.replace(marker, value)
    return html


def build(force: bool = False, docs: Path = DOCS, page_only: bool = False) -> list[Path]:
    """Write the page and its three layers to ``docs`` (only the page with ``page_only``, which
    needs neither the scenes nor the built-up raster); returns the written paths."""
    import matplotlib.image

    docs = Path(docs)
    outputs = [docs / "index.html"]
    if not page_only:
        outputs += [docs / p for p in LAYERS.values()]
    existing = [p.relative_to(docs).as_posix() for p in outputs if p.exists()]
    if existing and not force:
        raise FileExistsError(f"{existing} exist; use --force to replace them")
    if not (docs / "clases.png").exists():
        raise FileNotFoundError(f"{docs / 'clases.png'} missing: it is written by severidad.web")
    check_vendor(docs / "vendor" / "maplibre-gl")
    grid, corners = mercator_grid()
    data = viewer_data(corners)
    html = render(data)
    if page_only:
        (docs / "index.html").write_bytes(html.encode("utf-8"))
        logger.info("written index.html (%d bytes, sha256 %s)",
                    (docs / "index.html").stat().st_size, sha256(docs / "index.html")[:12])
        return outputs
    layers = {"built": built_outline(grid), "before": true_colour(estudio.PRE_SCENE, grid),
              "after": true_colour(estudio.POST_SCENE, grid)}
    (docs / "capas").mkdir(parents=True, exist_ok=True)
    for key, image in layers.items():
        path = docs / LAYERS[key]
        if path.suffix == ".jpg":
            matplotlib.image.imsave(path, image, format="jpg",
                                    pil_kwargs={"quality": JPEG_QUALITY, "optimize": True})
        else:
            matplotlib.image.imsave(path, image, format="png")
    (docs / "index.html").write_bytes(html.encode("utf-8"))
    for path in outputs:
        logger.info("written %s (%d bytes, sha256 %s)", path.relative_to(docs).as_posix(),
                    path.stat().st_size, sha256(path)[:12])
    return outputs


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="replace the page and its layers")
    parser.add_argument("--solo-pagina", action="store_true",
                        help="write only docs/index.html; the layers are left as they are")
    args = parser.parse_args(argv)
    try:
        build(force=args.force, page_only=args.solo_pagina)
    except (FileExistsError, FileNotFoundError, ValueError) as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
