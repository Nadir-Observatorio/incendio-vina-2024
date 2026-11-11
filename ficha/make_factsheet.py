"""Two-page technical factsheet (A4), in English and Spanish, built only from the files in `datos/`.

    python ficha/make_factsheet.py                 # both languages; refuses to overwrite
    python ficha/make_factsheet.py --lang es       # one language
    python ficha/make_factsheet.py --force         # replaces existing files

Outputs: `ficha/factsheet.pdf` (English) and `ficha/ficha.pdf` (Spanish).

Every figure in the text is read from `datos/metricas.json`, `datos/superficies.csv` or
`datos/huellas.json`; the map is `datos/clases.tif` over the context layers of `datos/contexto/`
(relief, water, the 2021 built-up outline, city names and the locator, written by
`severidad.contexto`). Municipal
boundaries are not drawn: the official layer asks for derived products to be validated by DIFROL.
Class colours come from `severidad.estudio`, the same as the web map. The PDF carries no creation
date, so the same inputs give the same file on one system; across systems compare the content
hash that the script prints (`pdf_content_hash`), because zlib compresses the same content to
different bytes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import logging
import re
import sys
import textwrap
import zlib
from collections import defaultdict
from pathlib import Path

from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform as shp_transform

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from severidad.estudio import PALETTE, POST_SCENE, PRE_SCENE  # noqa: E402

logger = logging.getLogger("factsheet")

DATA = ROOT / "datos"
CONTEXT = DATA / "contexto"
OUTPUTS = {"en": ROOT / "ficha" / "factsheet.pdf", "es": ROOT / "ficha" / "ficha.pdf"}
A4 = (8.27, 11.69)
INK, SOFT, ACCENT, LINE = "#1d2327", "#5b6670", "#a50026", "#d9dee2"
LAND_RGB, WATER_RGB, SEA_INK = (242, 239, 233), (214, 228, 238), "#5b7f99"
BUILT_INK, BUILT_LW = "#3d454c", 0.3     # thin outline: must not compete with the severity classes
HALO = [pe.withStroke(linewidth=2.4, foreground="white")]
SEA, INLAND = 1, 2

TEXT = {
    "en": {
        "pdf_title": "Burn severity of the Vina del Mar fire, February 2024",
        "title": "Burn severity of the Viña del Mar fire, February 2024",
        "subtitle": "Valparaíso, Viña del Mar, Quilpué and Villa Alemana, Chile  ·  Sentinel-2 "
                    "dNBR, validated against Copernicus EMS\nCamilo López Barra, geographer",
        "headline": "burned according to the dNBR threshold calibrated locally\nagainst "
                    "Copernicus EMSR715 ({thr}; 95 % interval {lo} to {hi} ha).\nEMSR715: {ref} "
                    "ha. The rule fixed before measuring gave\n{b} ha, within a range up to {rng} "
                    "ha (page 2).",
        "ocean": "Pacific\nOcean", "scale": "1:{s} printed on A4", "map_area": "Map\narea",
        "legend": "SEVERITY (Key and Benson 2006, limits not calibrated for central Chile)",
        "classes": {7: "High", 6: "Moderate-high", 5: "Moderate-low", 4: "Low"},
        "other": "OTHER CLASSES", "regrowth": "Regrowth", "water": "Water", "unburned": "Unburned",
        "built": "Built-up 2021",
        "sources": "UTM zone 19 S coordinates (km), WGS 84. Cities: GeoNames (CC BY 4.0). "
                   "Water: Sentinel-2 scene classification. Built-up 2021 (reference, see O5): "
                   "ESA WorldCover 2021 v200, class 50, \u00a9 ESA WorldCover project 2021 / "
                   "Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover "
                   "consortium. Relief "
                   "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus "
                   "Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European "
                   "Union and ESA; all rights reserved. Locator: Natural Earth. 20 m pixels.",
        "table_title": "BURNED AREA BY MUNICIPALITY (ha)",
        "table_cols": ["Municipality", "Low", "Moderate-low", "Moderate-high", "High",
                       "Moderate-low\nor higher", "All burned\nclasses"],
        "tiles": ["accuracy against EMSR715\n(original rule: {b} and {a})",
                  "commission\n(original rule: {b} and {a})",
                  "omission\n(original rule: {b} and {a})",
                  "of damaged buildings (EMSN188)\nin burned ({a} with all classes)"],
        "validation_cal": "VALIDATION OF THE CALIBRATED THRESHOLD ON BLOCKS THAT TOOK NO PART "
                          "IN THE FIT",
        "attribution": "Contains modified Copernicus Sentinel data 2024. Validation: Copernicus "
                       "Emergency "
                       "Management Service (© 2024 European Union), EMSR715 and EMSN188. Relief: "
                       "Copernicus WorldDEM-30. Built-up: ESA WorldCover 2021.\n"
                       "Code MIT, derived data CC BY 4.0. Independent "
                       "analysis, not "
                       "endorsed by any of the data providers.",
        "p2_title": "Method and validation", "method": "Method",
        "steps": [
            "Scenes: Sentinel-2 L2A {pre} (pre-fire) and {post} (post-fire) from the Earth Search "
            "STAC "
            "catalogue; the least cloudy scene inside the study area in each window, a rule fixed "
            "before "
            "looking at results. Bands B8A, B12 and scene classification, 20 m.",
            "Reflectance: the +1000 digital-number offset is measured on water pixels instead of "
            "taken "
            "from the catalogue metadata (see O4).",
            "Quality mask: no data, saturated, dark, shadow, cloud, cirrus, water and snow "
            "removed from "
            "the pre-fire scene; dark pixels and shadows kept in the post-fire scene (burn scars "
            "are dark).",
            "dNBR with offset: NBR = (NIR - SWIR) / (NIR + SWIR); dNBR = NBR pre - NBR post, "
            "minus the "
            "mean dNBR of an unburned ring 500 m to 2 km around the preliminary burned area "
            "(Parks et al. "
            "2018).",
            "Classes: Key and Benson (2006), dNBR x 1000: low 100 to 270, moderate-low 270 to 440, "
            "moderate-high 440 to 660, high 660 or more; unburned -100 to 100.",
            "Reproducibility: the content hash of the class raster ({h}...) is identical when "
            "rebuilt "
            "from scratch on Windows and Linux.",
        ],
        "val_heading": "Validation of the original rule (protocol fixed before measuring)",
        "m1": "M1, omission in the urban interface: {n} of the {u} building damage points of "
              "Copernicus "
              "EMSN188 (Pléiades Neo, 0.3 m, 'Las Tablas' area), drawn with seed {seed} and at "
              "least "
              "100 m apart. {pa} fall in any burned class (Wilson 95 %: {wa0} to {wa1}); {pb} at "
              "moderate-low or higher.",
        "m2": "M2, burned / unburned against the EMSR715 delineation v2 (WorldView-2, 2 m), "
              "inside the "
              "WorldView-2 footprint ({dom} ha; {ref} ha burned in the reference). 300 points per "
              "predicted "
              "stratum, at least 200 m apart; accuracy and omission weighted by stratum area "
              "(Olofsson et "
              "al. 2014).",
        "m2_cols": ["Threshold", "Predicted burned (ha)", "Area-weighted accuracy",
                    "Commission (Wilson 95 %)", "Area-weighted omission"],
        "thr": {"B": "Moderate-low or higher", "A": "All burned classes"}, "to": "to",
        "cal_heading": "Local calibration of the threshold (amendment of {date})",
        "cal": "Against EMSR715, the burned / unburned cut was calibrated on 2 km blocks ({nf} "
               "to fit, {nv} to validate, without the M2 pixels): dNBR threshold {thr} ({t0} to "
               "{t1}). On validation: accuracy {acc}, commission {com}, omission {om}, area "
               "difference {da} against {bda} for the original rule; EMSN188 buildings in burned: "
               "{e} of {n}. Protocol fixed before computing; making it the headline was decided "
               "after seeing the results (README.md).",
        "rule": "Original rule: publish the threshold with the higher area-weighted accuracy and "
                "the other as the range; moderate-low or higher won by {d} points. EMSR715 used "
                "Sentinel-2 as its pre-event reference, so it is mostly, not fully, independent.",
        "lim_heading": "Limitations",
        "lims": [
            "O1. Vegetation, not buildings. About {o1} % of the buildings mapped as damaged by "
            "EMSN188 "
            "fall outside every burned class: dNBR at 20 m measures loss of vegetation, and "
            "building "
            "damage needs very high resolution imagery.",
            "O2. Low severity adds commission. Including it raises commission from {cb} to {ca}; "
            "leaving it out raises omission from {oa} to {ob}. Hence the calibration above.",
            "O3. Uncalibrated classes. Key and Benson limits were defined for North American "
            "ecosystems; no field severity plots were available for central Chile.",
            "O4. Catalogue offset. The Earth Search COGs of these scenes do not carry the +1000 "
            "offset "
            "their STAC metadata declares; applying it roughly doubles the burned area.",
            "O5. Built-up 2021 is a reference outline (ESA WorldCover class 50: buildings, roads, "
            "structures); no figure uses it. It misses later settlements and can under-represent "
            "scattered self-built housing.",
        ],
        "refs_heading": "References",
    },
    "es": {
        "pdf_title": "Severidad del incendio de Vina del Mar, febrero de 2024",
        "title": "Severidad del incendio de Viña del Mar, febrero de 2024",
        "subtitle": "Valparaíso, Viña del Mar, Quilpué y Villa Alemana, Chile  ·  dNBR "
                    "Sentinel-2, validado con Copernicus EMS\nCamilo López Barra, geógrafo",
        "headline": "quemadas según el umbral dNBR calibrado localmente con\nCopernicus "
                    "EMSR715 ({thr}; intervalo del 95 %: {lo} a {hi} ha).\nEMSR715: {ref} ha. La "
                    "regla fijada antes de medir daba\n{b} ha, con un rango de hasta {rng} ha "
                    "(página 2).",
        "ocean": "Océano\nPacífico",
        "scale": "1:{s} impreso en A4",
        "map_area": "Área del\nmapa",
        "legend": "SEVERIDAD (Key y Benson 2006, límites sin calibrar para Chile central)",
        "classes": {7: "Alta", 6: "Moderada-alta", 5: "Moderada-baja", 4: "Baja"},
        "other": "OTRAS CLASES", "regrowth": "Rebrote", "water": "Agua", "unburned": "Sin quemar",
        "built": "Construido 2021",
        "sources": "Coordenadas UTM huso 19 S (km), WGS 84. Ciudades: GeoNames (CC BY 4.0). "
                   "Agua: clasificación de escena Sentinel-2. Construido 2021 (referencia, ver "
                   "O5): ESA WorldCover 2021 v200, clase 50, \u00a9 ESA WorldCover project 2021 "
                   "/ Contains modified Copernicus Sentinel data (2021) processed by ESA "
                   "WorldCover consortium. Relieve producido con Copernicus WorldDEM-30 © DLR e.V. "
                   "2010-2014 "
                   "y © Airbus Defence and Space GmbH 2014-2018 provisto bajo COPERNICUS por la "
                   "Unión Europea y la ESA; todos los derechos reservados. Ubicación: Natural "
                   "Earth. Píxel de 20 m.",
        "table_title": "SUPERFICIE QUEMADA POR COMUNA (ha)",
        "table_cols": ["Comuna", "Baja", "Moderada-baja", "Moderada-alta", "Alta",
                       "Moderada-baja\no mayor", "Todas las\nclases"],
        "tiles": ["exactitud frente a EMSR715\n(regla original: {b} y {a})",
                  "comisión\n(regla original: {b} y {a})",
                  "omisión\n(regla original: {b} y {a})",
                  "de las edificaciones dañadas\n(EMSN188) en quemado ({a} con todas)"],
        "validation_cal": "VALIDACIÓN DEL UMBRAL CALIBRADO EN BLOQUES QUE NO PARTICIPARON DEL "
                          "AJUSTE",
        "attribution": "Contiene datos modificados de Copernicus Sentinel 2024. Validación: "
                       "Copernicus "
                       "Emergency Management Service (© 2024 Unión Europea), EMSR715 y EMSN188. "
                       "Relieve: Copernicus WorldDEM-30. Construido: ESA WorldCover 2021.\n"
                       "Código MIT, datos derivados CC BY 4.0. "
                       "Análisis "
                       "independiente, sin respaldo de los proveedores de datos.",
        "p2_title": "Método y validación", "method": "Método",
        "steps": [
            "Escenas: Sentinel-2 L2A {pre} (antes del incendio) y {post} (después) del catálogo "
            "STAC "
            "de Earth Search; en cada ventana, la escena con menos nubes dentro del área de "
            "estudio, regla "
            "fijada antes de mirar resultados. Bandas B8A, B12 y clasificación de escena, 20 m.",
            "Reflectancia: el desplazamiento de +1000 en los números digitales se mide sobre "
            "píxeles de "
            "agua en vez de tomarlo de los metadatos del catálogo (ver O4).",
            "Máscara de calidad: sin datos, saturados, oscuros, sombra, nube, cirros, agua y "
            "nieve se "
            "eliminan de la escena previa; en la posterior se conservan píxeles oscuros y sombras "
            "(las "
            "cicatrices de fuego son oscuras).",
            "dNBR con desplazamiento: NBR = (NIR - SWIR) / (NIR + SWIR); dNBR = NBR previo - NBR "
            "posterior, "
            "menos el dNBR medio de un anillo sin quemar de 500 m a 2 km alrededor del área "
            "quemada "
            "preliminar (Parks et al. 2018).",
            "Clases: Key y Benson (2006), dNBR x 1000: baja 100 a 270, moderada-baja 270 a 440, "
            "moderada-alta 440 a 660, alta 660 o más; sin quemar -100 a 100.",
            "Reproducibilidad: la huella de contenido del ráster de clases ({h}...) es idéntica al "
            "rehacerlo desde cero en Windows y en Linux.",
        ],
        "val_heading": "Validación de la regla original (protocolo fijado antes de medir)",
        "m1": "M1, omisión en la interfaz urbana: {n} de los {u} puntos de daño a edificaciones de "
              "Copernicus EMSN188 (Pléiades Neo, 0,3 m, área 'Las Tablas'), sorteados con semilla "
              "{seed} y a 100 m o más entre sí. {pa} cae en alguna clase quemada (Wilson 95 %: "
              "{wa0} a "
              "{wa1}); {pb} en moderada-baja o mayor.",
        "m2": "M2, quemado / no quemado frente a la delimitación EMSR715 v2 (WorldView-2, 2 m), "
              "dentro de "
              "la huella de WorldView-2 ({dom} ha; {ref} ha quemadas en la referencia). 300 "
              "puntos por "
              "estrato predicho, a 200 m o más entre sí; exactitud y omisión ponderadas por el "
              "área "
              "de cada estrato (Olofsson et al. 2014).",
        "m2_cols": ["Umbral", "Quemado predicho (ha)", "Exactitud ponderada",
                    "Comisión (Wilson 95 %)", "Omisión ponderada"],
        "thr": {"B": "Moderada-baja o mayor", "A": "Todas las clases quemadas"}, "to": "a",
        "cal_heading": "Calibración local del umbral (enmienda del {date})",
        "cal": "Contra EMSR715 se calibró el corte quemado / no quemado en bloques de 2 km "
               "({nf} de ajuste, {nv} de validación, sin los píxeles de M2): umbral dNBR {thr} "
               "({t0} a {t1}). En validación: exactitud {acc}, comisión {com}, omisión {om}, "
               "diferencia de área {da} frente a {bda} de la regla original; edificaciones de "
               "EMSN188 en quemado: {e} de {n}. Protocolo fijado antes de calcular; su uso como "
               "cifra principal se decidió después de ver los resultados (README.md).",
        "rule": "Regla original: publicar el umbral con mayor exactitud ponderada y el otro "
                "como rango; ganó moderada-baja o mayor por {d} puntos. EMSR715 usó Sentinel-2 "
                "como referencia previa, así que es independiente de este mapa en buena parte.",
        "lim_heading": "Limitaciones",
        "lims": [
            "O1. Vegetación, no edificaciones. Cerca del {o1} % de las edificaciones que EMSN188 "
            "marca "
            "como dañadas quedan fuera de toda clase quemada: el dNBR a 20 m mide pérdida de "
            "vegetación, "
            "y el daño a edificaciones exige imágenes de muy alta resolución.",
            "O2. La severidad baja suma comisión. Incluirla sube la comisión de {cb} a {ca}; "
            "dejarla fuera sube la omisión de {oa} a {ob}. Por eso la calibración de arriba.",
            "O3. Clases sin calibrar. Los límites de Key y Benson se definieron para ecosistemas "
            "de Norteamérica; no hubo parcelas de severidad en terreno para Chile central.",
            "O4. Desplazamiento del catálogo. Los COG de Earth Search de estas escenas no traen el "
            "desplazamiento de +1000 que declaran sus metadatos STAC; aplicarlo duplica "
            "aproximadamente el "
            "área quemada.",
            "O5. Construido 2021 es un contorno de referencia (clase 50 de ESA WorldCover: "
            "edificios, caminos, estructuras); ninguna cifra lo usa. No incluye lo posterior y "
            "puede subrepresentar la autoconstrucción dispersa.",
        ],
        "refs_heading": "Referencias",
    },
}
REFERENCES = [
    "Key, C. H., and Benson, N. C. (2006). Landscape assessment (LA): sampling and analysis "
    "methods. "
    "FIREMON. USDA Forest Service, RMRS-GTR-164-CD.",
    "Olofsson, P., et al. (2014). Good practices for estimating area and assessing accuracy of "
    "land "
    "change. Remote Sensing of Environment, 148, 42-57.",
    "Parks, S. A., et al. (2018). Mean composite fire severity metrics computed with Google Earth "
    "Engine offer improved accuracy and expanded mapping potential. Remote Sensing, 10(6), 879.",
    "Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. "
    "Journal of the American Statistical Association, 22(158), 209-212.",
    "Zanaga, D., et al. (2022). ESA WorldCover 10 m 2021 v200. Zenodo. "
    "doi:10.5281/zenodo.7254221.",
]


def num(v: float, lang: str, dec: int = 0) -> str:
    """Thousands and decimal separators of the language (English 6,759.7; Spanish 6.759,7)."""
    s = f"{v:,.{dec}f}"
    return s if lang == "en" else s.translate(str.maketrans({",": ".", ".": ","}))


def pct(v: float, lang: str) -> str:
    return f"{num(v, lang, 1)} %"


def rgb(code: int) -> tuple[float, float, float]:
    return tuple(c / 255 for c in PALETTE[code][:3])


def paragraph(fig: Figure, x: float, y: float, s: str, width: int, size: float = 7.8,
              **kw: Any) -> float:
    """Wrapped paragraph at figure coordinates; returns the y below it."""
    lines = textwrap.wrap(s, width)
    fig.text(x, y, "\n".join(lines), fontsize=size, va="top", color=kw.pop("color", INK),
             linespacing=1.35, **kw)
    return y - len(lines) * size * 1.35 / 72 / A4[1] - 0.006


def heading(fig: Figure, x: float, y: float, s: str, width: float = 0.86, size: float = 7.2,
            upper: bool = True) -> float:
    fig.text(x, y, s.upper() if upper else s, fontsize=size, color=SOFT, weight="bold", va="top")
    fig.add_artist(plt.Line2D([x, x + width], [y - 0.016, y - 0.016], color=LINE, lw=0.8))
    return y - 0.026


def load() -> dict:
    """Everything the factsheet reads, from `datos/` only."""
    metrics = json.loads((DATA / "metricas.json").read_text(encoding="utf-8"))
    hashes = json.loads((DATA / "huellas.json").read_text(encoding="utf-8"))
    areas: dict[str, dict[int, float]] = defaultdict(dict)
    with (DATA / "superficies.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            areas[row["municipality"]][int(row["class_code"])] = float(row["hectares"])
    with rasterio.open(DATA / "clases.tif") as r:
        classes, b, crs, transform = r.read(1), r.bounds, r.crs, r.transform
    rasters = {}
    for name in ("agua", "relieve", "construido"):
        path = CONTEXT / f"{name}.tif"
        if not path.exists():
            raise FileNotFoundError(f"{path.name} missing in {path.parent}: run "
                                    "'python -m severidad.contexto derivar'")
        with rasterio.open(path) as r:
            if r.transform != transform or r.crs != crs or r.shape != classes.shape:
                raise ValueError(f"{path.name} is not on the grid of clases.tif")
            rasters[name] = r.read(1)
    to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    lugares = json.loads((CONTEXT / "lugares.geojson").read_text(encoding="utf-8"))
    cities = [(f["properties"]["NAME"], shp_transform(to_utm, shape(f["geometry"])))
              for f in lugares["features"]]
    countries = json.loads((CONTEXT / "chile.geojson").read_text(encoding="utf-8"))
    adjustment = json.loads((DATA / "ajuste_umbrales.json").read_text(encoding="utf-8"))
    return {"metrics": metrics, "adjustment": adjustment, "hashes": hashes,
            "areas": dict(areas), "classes": classes,
            "extent": (b.left, b.right, b.bottom, b.top), "crs": crs, "water": rasters["agua"],
            "relief": rasters["relieve"], "built": rasters["construido"], "cities": cities,
            "countries": countries}


def background(relief: np.ndarray, water: np.ndarray) -> np.ndarray:
    """Land tinted by the hillshade (soft shadows, sunny slopes almost white); water flat."""
    shade = np.where(relief > 0, (relief.astype(float) - 1) / 254, 0.7)
    light = 0.97 + 0.30 * (shade - 0.7)
    img = np.empty((*relief.shape, 4), dtype=np.uint8)
    img[..., :3] = np.clip(np.array(LAND_RGB, float)[None, None, :] * light[..., None], 0, 255)
    img[..., 3] = 255
    img[(water == SEA) | (water == INLAND)] = (*WATER_RGB, 255)
    return img


def draw_map(fig: Figure, rect: list[float], d: dict, T: dict, lang: str) -> None:
    ext, classes = d["extent"], d["classes"]
    ax = fig.add_axes(rect)
    ax.imshow(background(d["relief"], d["water"]), extent=ext, interpolation="nearest", zorder=1)
    lut = np.zeros((256, 4), dtype=np.uint8)
    for code, colour in PALETTE.items():
        lut[code] = (*colour[:3], 0 if colour[3] == 0 else 255)
    ax.imshow(lut[classes], extent=ext, interpolation="nearest", zorder=2)
    # Built-up outline on pixel edges is drawn through pixel centres; at 20 m that is invisible on
    # paper and keeps the outline a line, never a fill.
    ax.contour(d["built"], levels=[0.5], colors=BUILT_INK, linewidths=BUILT_LW, origin="upper",
               extent=ext, zorder=3)
    frame = box(ext[0], ext[2], ext[1], ext[3])
    for name, point in d["cities"]:
        if not frame.contains(point):
            continue
        ax.plot(point.x, point.y, "o", ms=3.4, mfc="white", mec=INK, mew=0.9, zorder=6)
        ax.text(point.x + 450, point.y + 350, name, fontsize=7.4, color="#2b3238", ha="left",
                va="bottom", weight="bold", zorder=6, path_effects=HALO)
    rows, cols = np.nonzero(d["water"] == SEA)
    if len(cols):
        sea_x = ext[0] + (np.percentile(cols, 15) + 0.5) * 20
        sea_y = ext[3] - (np.percentile(rows, 70) + 0.5) * 20
        ax.text(sea_x, sea_y,
                T["ocean"], fontsize=8, color=SEA_INK, style="italic", ha="center", va="center",
                zorder=6, linespacing=1.1)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    xt = np.arange(np.ceil(ext[0] / 5000) * 5000, ext[1], 5000)
    yt = np.arange(np.ceil(ext[2] / 5000) * 5000, ext[3], 5000)
    ax.set_xticks(xt)
    ax.set_yticks(yt)
    ax.set_xticklabels([f"{v / 1000:.0f}" for v in xt], fontsize=6, color=SOFT)
    ax.set_yticklabels([f"{v / 1000:.0f}" for v in yt], fontsize=6, color=SOFT, rotation=90,
                       va="center")
    ax.tick_params(length=3, width=0.6, color=SOFT, top=True, right=True, labeltop=True,
                   labelright=True, pad=1.5)
    ax.grid(color="white", lw=0.4, alpha=0.7, zorder=5)
    for s in ax.spines.values():
        s.set_color(INK)
        s.set_linewidth(0.8)
    # Graphic scale 0-1-2-5 km, scale computed from the printed width, and north arrow.
    x0, y0 = ext[0] + 1400, ext[2] + 1300
    ax.add_patch(Rectangle((x0 - 500, y0 - 750), 6200, 1650, facecolor="white", alpha=0.85,
                           edgecolor="none", zorder=7))
    for i, (a, b) in enumerate(((0, 1000), (1000, 2000), (2000, 5000))):
        ax.add_patch(Rectangle((x0 + a, y0), b - a, 220, facecolor=INK if i % 2 == 0 else "white",
                               edgecolor=INK, lw=0.6, zorder=8))
    for v in (0, 1, 2, 5):
        ax.text(x0 + v * 1000, y0 + 380, f"{v}", fontsize=5.8, ha="center", color=INK, zorder=8)
    ax.text(x0 + 5250, y0 + 110, "km", fontsize=5.8, va="center", color=INK, zorder=8)
    scale = round((ext[1] - ext[0]) / (rect[2] * A4[0] * 0.0254) / 5000) * 5000
    ax.text(x0 + 2500, y0 - 420, T["scale"].format(s=num(scale, lang)), fontsize=5, ha="center",
            color=SOFT, zorder=8)
    ax.annotate("N", xy=(0.965, 0.955), xytext=(0.965, 0.875), xycoords="axes fraction",
                ha="center",
                fontsize=8, weight="bold", color=INK, zorder=8, path_effects=HALO,
                arrowprops={"arrowstyle": "-|>,head_width=0.35", "color": INK, "lw": 1.1})
    draw_locator(fig, rect, d, T)


def draw_locator(fig: Figure, rect: list[float], d: dict, T: dict) -> None:
    """Chile and its neighbours with the map area, in the lower right corner of the map."""
    view = (-73.6, -36.2, -66.9, -29.6)
    w, h = 0.135, 0.17
    ins = fig.add_axes([rect[0] + rect[2] - w - 0.006, rect[1] + 0.006, w, h])
    ins.set_facecolor(tuple(c / 255 for c in WATER_RGB))
    frame = box(*view)
    for f in d["countries"]["features"]:
        geom, chile = shape(f["geometry"]), f["properties"]["ADM0_A3"] == "CHL"
        for part in getattr(geom, "geoms", [geom]):
            x, y = part.exterior.xy
            ins.fill(x, y, color="#fbfaf7" if chile else "#e6e3dc", lw=0)
            ins.plot(x, y, color="#8a939b", lw=0.4)
        # Chile is labelled in the north third of the view, away from the map-area box.
        inside = geom.intersection(box(view[0], view[3] - (view[3] - view[1]) / 3, view[2], view[3])
                                   if chile else frame)
        if not inside.is_empty and f["properties"]["ADM0_A3"] in ("CHL", "ARG"):
            p = inside.representative_point()
            ins.text(p.x, p.y, f["properties"]["NAME"].upper(), fontsize=5 if chile else 4.6,
                     color="#3a4249" if chile else "#7a838b", weight="bold" if chile else "normal",
                     ha="center", va="center", rotation=90 if not chile else 0)
    to_ll = Transformer.from_crs(d["crs"], "EPSG:4326", always_xy=True).transform
    ext = d["extent"]
    (x0, y0), (x1, y1) = to_ll(ext[0], ext[2]), to_ll(ext[1], ext[3])
    ins.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=ACCENT, edgecolor=ACCENT, lw=1.0))
    ins.text(x0 - 0.15, (y0 + y1) / 2, T["map_area"], fontsize=4.6, color=ACCENT, ha="right",
             va="center", path_effects=HALO)
    ins.set_xlim(view[0], view[2])
    ins.set_ylim(view[1], view[3])
    ins.set_aspect(1 / np.cos(np.radians(33)))
    ins.set_xticks([])
    ins.set_yticks([])
    for s in ins.spines.values():
        s.set_color(INK)
        s.set_linewidth(0.7)


def footer(fig: Figure, T: dict) -> None:
    """Attribution at the foot of every page, wrapped to the text width."""
    lines = textwrap.wrap(T["attribution"].replace("\n", " "), 168)
    fig.text(0.07, 0.044, "\n".join(lines), fontsize=5.9, color=SOFT, va="top", linespacing=1.4)


def swatch(fig: Figure, x: float, y: float, colour: Any, label: str) -> float:
    """Legend entry: a filled box, or the built-up line when ``colour`` is None. Returns the x
    where the label ends, measured on the drawn figure."""
    if colour is None:
        fig.add_artist(plt.Line2D([x, x + 0.022], [y - 0.0065, y - 0.0065], color=BUILT_INK,
                                  lw=0.8, transform=fig.transFigure))
    else:
        fig.add_artist(Rectangle((x, y - 0.012), 0.022, 0.011, transform=fig.transFigure,
                                 facecolor=colour, edgecolor="#9aa3aa", lw=0.4))
    text = fig.text(x + 0.027, y - 0.0065, label, fontsize=6.8, color=INK, va="center")
    renderer = fig.canvas.get_renderer()
    return float(text.get_window_extent(renderer).x1 / fig.bbox.width)


def page_results(pdf: PdfPages, d: dict, lang: str) -> None:
    T = TEXT[lang]
    metrics = d["metrics"]
    m2, head = metrics["measurement_2"], metrics["headline"]
    ext = d["extent"]
    fig = plt.figure(figsize=A4)
    fig.text(0.07, 0.962, T["title"], fontsize=16.5, weight="bold", color=INK, va="top")
    fig.text(0.07, 0.934, T["subtitle"], fontsize=7.8, color=SOFT, va="top", linespacing=1.35)
    fig.add_artist(Rectangle((0.07, 0.851), 0.005, 0.053, color=ACCENT, transform=fig.transFigure))
    # One decimal, as in datos/metricas.json, the table below and the web viewer.
    adj = d["adjustment"]
    chosen = adj["chosen"]
    big = fig.text(0.085, 0.904, f"{num(chosen['hectares'], lang, 1)} ha", fontsize=27,
                   weight="bold", color=INK, va="top")
    right = big.get_window_extent(fig.canvas.get_renderer()).x1 / fig.bbox.width
    lead = fig.text(max(0.355, right + 0.025), 0.901,
                    T["headline"].format(thr=chosen["threshold"],
                                         lo=num(chosen["hectares_interval"][0], lang, 1),
                                         hi=num(chosen["hectares_interval"][1], lang, 1),
                                         ref=num(m2["reference_burned_ha"], lang, 1),
                                         b=num(head["hectares"], lang, 1),
                                         rng=num(head["range_up_to_hectares"], lang, 1)),
                    fontsize=8.4, color=INK, va="top", linespacing=1.3)
    edge = lead.get_window_extent(fig.canvas.get_renderer()).x1 / fig.bbox.width
    if edge > 0.93:
        raise ValueError(f"headline text overflows the right margin (x = {edge:.3f})")

    height = 0.86 * (ext[3] - ext[2]) / (ext[1] - ext[0]) * A4[0] / A4[1]
    draw_map(fig, [0.07, 0.832 - height, 0.86, height], d, T, lang)

    yl = 0.832 - height - 0.037
    fig.text(0.07, yl + 0.012, T["legend"], fontsize=6.3, color=SOFT, weight="bold", va="bottom")
    for i, code in enumerate((7, 6, 5, 4)):
        swatch(fig, 0.07 + i * 0.125, yl, rgb(code), T["classes"][code])
    x_other = 0.53
    fig.text(x_other, yl + 0.012, T["other"], fontsize=6.3, color=SOFT, weight="bold",
             va="bottom")
    # Placed one after another by their measured width, so longer labels never overlap.
    x = x_other
    for colour, label in ((rgb(2), T["regrowth"]), (tuple(c / 255 for c in WATER_RGB), T["water"]),
                          (tuple(c / 255 for c in LAND_RGB), T["unburned"]),
                          (None, T["built"])):
        x = swatch(fig, x, yl, colour, label) + 0.016
    if x - 0.016 > 0.93:
        raise ValueError(f"legend row overflows the right margin (x = {x - 0.016:.3f})")
    ys = paragraph(fig, 0.07, yl - 0.026, T["sources"], 160, size=5.8, color=SOFT)

    yt = heading(fig, 0.07, ys - 0.002, T["table_title"], upper=False)
    cells, totals = [], np.zeros(6)
    for name, a in d["areas"].items():
        values = [a[4], a[5], a[6], a[7], a[5] + a[6] + a[7], a[4] + a[5] + a[6] + a[7]]
        totals += values
        cells.append([name] + [num(v, lang, 1) for v in values])
    cells.append(["Total"] + [num(v, lang, 1) for v in totals])
    tax = fig.add_axes([0.07, yt - 0.140, 0.86, 0.14])
    tax.axis("off")
    table = tax.table(cellText=cells, colLabels=T["table_cols"], loc="upper center",
                      cellLoc="right",
                      colLoc="right", edges="horizontal")
    table.auto_set_font_size(False)
    table.set_fontsize(7.4)
    table.scale(1, 1.08)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor(LINE)
        cell.set_linewidth(0.6)
        if r == 0:
            cell.set_text_props(color=SOFT, fontsize=6.6)
            cell.set_height(cell.get_height() * 1.55)
        if c == 0:
            cell._loc = "left"
            cell.set_text_props(ha="left")
        if r == len(cells):
            cell.set_text_props(weight="bold")

    # Validation goes below the real bottom of the table, measured on the drawn figure.
    renderer = fig.canvas.get_renderer()
    bottom = table.get_window_extent(renderer).y0 / fig.bbox.height
    yv = heading(fig, 0.07, bottom - 0.018, T["validation_cal"])
    if yv - 0.062 < 0.050:
        raise ValueError(f"page 1 validation overflows into the footer (y = {yv - 0.062:.3f})")
    v = adj["indices"][chosen["index"]]["validation"]
    pub = adj["published_thresholds_on_validation"]
    e = adj["emsn188"]
    tiles = [(pct(v[k], lang), T["tiles"][i].format(b=pct(pub["B"][k], lang),
                                                    a=pct(pub["A"][k], lang)))
             for i, k in enumerate(("accuracy_pct", "commission_pct", "omission_pct"))]
    tiles.append((pct(e[chosen["index"]]["pct"], lang),
                  T["tiles"][3].format(a=pct(e["A"]["pct"], lang))))
    for i, (big, small) in enumerate(tiles):
        x = 0.07 + i * 0.215
        fig.text(x, yv, big, fontsize=15, weight="bold", color=INK, va="top")
        fig.text(x, yv - 0.027, small, fontsize=6.4, color=SOFT, va="top", linespacing=1.3)
    footer(fig, T)
    fig.text(0.93, 0.02, "1 / 2", fontsize=6.3, color=SOFT, ha="right")
    pdf.savefig(fig)
    plt.close(fig)


def page_method(pdf: PdfPages, d: dict, lang: str) -> None:
    T = TEXT[lang]
    metrics = d["metrics"]
    p, m1, m2 = metrics["protocol"], metrics["measurement_1"], metrics["measurement_2"]
    fig = plt.figure(figsize=A4)
    fig.text(0.07, 0.962, T["p2_title"], fontsize=15, weight="bold", color=INK, va="top")
    y = heading(fig, 0.07, 0.928, T["method"])
    # Scene ids without the processing suffix ("_0_L2A"), as in the catalogue search.
    fill = {"pre": PRE_SCENE.rsplit("_", 2)[0], "post": POST_SCENE.rsplit("_", 2)[0],
            "h": d["hashes"]["classes_content_sha256"][:12]}
    for i, s in enumerate(T["steps"], 1):
        fig.text(0.07, y, f"{i}.", fontsize=7.8, color=SOFT, va="top")
        y = paragraph(fig, 0.095, y, s.format(**fill), 122)

    y = heading(fig, 0.07, y - 0.006, T["val_heading"])
    y = paragraph(fig, 0.07, y, T["m1"].format(
        n=m1["sample"], u=num(m1["universe"], lang), seed=p["seed"], pa=pct(m1["A"]["pct"], lang),
        wa0=num(m1["A"]["wilson"][0], lang, 1), wa1=num(m1["A"]["wilson"][1], lang, 1),
        pb=pct(m1["B"]["pct"], lang)), 128)
    y = paragraph(fig, 0.07, y, T["m2"].format(dom=num(m2["domain_ha"], lang, 1),
                                                ref=num(m2["reference_burned_ha"], lang, 1)), 128)
    tax = fig.add_axes([0.07, y - 0.085, 0.86, 0.08])
    tax.axis("off")
    rows = []
    for key in ("B", "A"):
        r = m2[key]
        rows.append([T["thr"][key], num(r["predicted_burned_ha"], lang, 1),
                     pct(r["weighted_accuracy_pct"], lang),
                     f"{pct(r['commission_pct'], lang)} "
                     f"({num(r['commission_wilson'][0], lang, 1)} {T['to']} "
                     f"{num(r['commission_wilson'][1], lang, 1)})",
                     pct(r["weighted_omission_pct"], lang)])
    table = tax.table(cellText=rows, colLabels=T["m2_cols"], loc="upper center", cellLoc="right",
                      colLoc="right", edges="horizontal")
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    table.scale(1, 1.35)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor(LINE)
        cell.set_linewidth(0.6)
        if c == 0:
            cell.set_text_props(ha="left")
            cell._loc = "left"
        if r == 0:
            cell.set_text_props(color=SOFT, fontsize=6.8)
    y -= 0.075
    diff = round(m2["B"]["weighted_accuracy_pct"] - m2["A"]["weighted_accuracy_pct"], 1)
    y = paragraph(fig, 0.07, y, T["rule"].format(d=num(diff, lang, 1)), 128)

    adj = d["adjustment"]
    c = adj["chosen"]
    v = adj["indices"][c["index"]]["validation"]
    pub = adj["published_thresholds_on_validation"]
    e = adj["emsn188"]
    day = "-".join(reversed(adj["date"].split("-"))) if lang == "es" else adj["date"]
    y = paragraph(fig, 0.07, y - 0.004, T["cal_heading"].format(date=day) + ". " + T["cal"].format(
        nf=adj["domain"]["fit_blocks"], nv=adj["domain"]["validation_blocks"],
        thr=c["threshold"], t0=c["threshold_interval"][0], t1=c["threshold_interval"][1],
        acc=pct(v["accuracy_pct"], lang), a0=num(v["accuracy_interval"][0], lang, 1),
        a1=num(v["accuracy_interval"][1], lang, 1), com=pct(v["commission_pct"], lang),
        om=pct(v["omission_pct"], lang), da=pct(v["area_difference_pct"], lang),
        d0=num(v["area_difference_interval"][0], lang, 1),
        d1=num(v["area_difference_interval"][1], lang, 1),
        bda=pct(pub["B"]["area_difference_pct"], lang),
        ada=pct(pub["A"]["area_difference_pct"], lang),
        e=pct(e[c["index"]]["pct"], lang), n=e["sample"],
        e0=num(e[c["index"]]["wilson"][0], lang, 1), e1=num(e[c["index"]]["wilson"][1], lang, 1)),
        128)

    y = heading(fig, 0.07, y - 0.006, T["lim_heading"])
    fill = {"o1": round(100 - m1["A"]["pct"]), "cb": pct(m2["B"]["commission_pct"], lang),
            "ca": pct(m2["A"]["commission_pct"], lang),
            "oa": pct(m2["A"]["weighted_omission_pct"], lang),
            "ob": pct(m2["B"]["weighted_omission_pct"], lang)}
    for s in T["lims"]:
        y = paragraph(fig, 0.07, y, s.format(**fill), 128)

    y = heading(fig, 0.07, y - 0.006, T["refs_heading"])
    for r in REFERENCES:
        y = paragraph(fig, 0.07, y, r, 142, size=6.8)

    footer(fig, T)
    fig.text(0.93, 0.02, "2 / 2", fontsize=6.3, color=SOFT, ha="right")
    if y < 0.06:
        raise ValueError(f"page 2 overflows into the footer (y = {y:.3f})")
    pdf.savefig(fig)
    plt.close(fig)


_OBJ = re.compile(rb"(\d+) 0 obj")
_LEN_REF = re.compile(rb"/Length (\d+) 0 R")
_LEN_DIRECT = re.compile(rb"/Length (\d+)(?![\d ]*0 R)")


def pdf_content_hash(path: Path) -> str:
    """sha256 of what the PDF draws, independent of how its streams were compressed.

    zlib gives different bytes on Windows and Linux for the same content, so the file hash of the
    factsheet differs between systems. This hash reads every object through the cross-reference
    table, decompresses the Flate streams and leaves out the stream lengths and the objects that
    only hold a length; text, drawing, images and fonts all count."""
    data = Path(path).read_bytes()
    start = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    rows = re.findall(rb"(\d{10}) (\d{5}) n", data[start:data.index(b"trailer", start)])
    offsets = [*sorted(int(o) for o, _gen in rows), start]
    objects: dict[int, bytes] = {}
    for a, b in itertools.pairwise(offsets):
        # Each object runs from its own offset to the next one (xref offsets are exact), so bytes
        # inside a compressed image can never be mistaken for the end of the object.
        m = _OBJ.match(data, a, b)
        if m is None:
            raise ValueError(f"{Path(path).name}: no object at byte {a}")
        body = data[m.end():b].strip()
        if not body.endswith(b"endobj"):
            raise ValueError(f"{Path(path).name}: object {m.group(1).decode()} is not closed")
        objects[int(m.group(1))] = body[: -len(b"endobj")].strip()
    length_objects = {int(n) for body in objects.values() for n in _LEN_REF.findall(body)}
    h = hashlib.sha256()
    for number in sorted(objects):
        if number in length_objects:
            continue
        body = objects[number]
        at = body.find(b"stream")
        if at < 0:
            h.update(b"%d|" % number + body)
            continue
        head = body[:at]
        ref = _LEN_REF.search(head)
        size = int(objects[int(ref.group(1))]) if ref else int(_LEN_DIRECT.search(head).group(1))
        begin = at + len(b"stream") + (2 if body[at + 6:at + 8] == b"\r\n" else 1)
        raw = body[begin:begin + size]
        content = zlib.decompress(raw) if b"/FlateDecode" in head else raw
        head = _LEN_DIRECT.sub(b"", _LEN_REF.sub(b"", head))
        h.update(b"%d|" % number + head + b"|" + content)
    return h.hexdigest()


def build(lang: str, out: Path | None = None, force: bool = False) -> Path:
    out = Path(out) if out is not None else OUTPUTS[lang]
    if out.exists() and not force:
        raise FileExistsError(f"{out.name} exists; use --force to replace it")
    d = load()
    metadata = {"Title": TEXT[lang]["pdf_title"], "Author": "Camilo Lopez Barra",
                "CreationDate": None,
                "Creator": None, "Producer": None}
    with PdfPages(out, metadata=metadata) as pdf:
        page_results(pdf, d, lang)
        page_method(pdf, d, lang)
    logger.info("written %s (content hash %s)", out.name, pdf_content_hash(out))
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=("en", "es", "all"), default="all")
    parser.add_argument("--force", action="store_true", help="replace existing PDFs")
    args = parser.parse_args(argv)
    for lang in (("en", "es") if args.lang == "all" else (args.lang,)):
        build(lang, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
