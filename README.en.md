*Leer en español: [README.md](README.md)*

# Burn severity of the Viña del Mar fire, February 2024

How much land burned, and how severely, in the fire of 2 and 3 February 2024 in Valparaíso, Viña del Mar, Quilpué and Villa Alemana (Chile)? This repository answers with two free Sentinel-2 scenes and an open method, **rebuilds the map from scratch on any computer and proves it with a content hash**, and validates it against two independent Copernicus Emergency Management Service references.

- **Interactive map:** [nadir-observatorio.github.io/incendios-severidad](https://nadir-observatorio.github.io/incendios-severidad/)
- **Technical factsheet (PDF, two A4 pages):** [English](ficha/factsheet.pdf) · [Spanish](ficha/ficha.pdf)
- **Sources and method (Spanish):** [nadir-observatorio.github.io/metodo.html](https://nadir-observatorio.github.io/metodo.html)
- **Sister case, building damage with Sentinel-1 radar:** [github.com/Nadir-Observatorio/incendios-dano-edificaciones](https://github.com/Nadir-Observatorio/incendios-dano-edificaciones)

<!-- results: written by herramientas/escribir_readme.py from datos/; do not edit by hand -->
## Results

**9,211.9 ha burned** according to the dNBR threshold calibrated locally against Copernicus EMSR715 (151; 95 % bootstrap interval: 8,634.5 to 9,582.1 ha). EMSR715 delineated 8,600.5 ha in the same area.

The rule fixed before measuring gave 6,759.7 ha at moderate-low severity or higher, within a range up to 10,663.0 ha if low severity is included. It is kept as the record in [`datos/metricas.json`](datos/metricas.json); the change of headline is explained below (amendment of 2026-11-11).

| Municipality | Low | Moderate-low | Moderate-high | High | Moderate-low or higher | All burned classes |
|---|---:|---:|---:|---:|---:|---:|
| Valparaíso | 1,656.2 | 1,154.4 | 798.0 | 330.8 | 2,283.2 | 3,939.4 |
| Viña del Mar | 865.7 | 815.9 | 990.4 | 589.1 | 2,395.4 | 3,261.1 |
| Quilpué | 651.0 | 556.1 | 595.6 | 371.0 | 1,522.6 | 2,173.6 |
| Villa Alemana | 730.4 | 361.5 | 140.0 | 57.0 | 558.4 | 1,288.9 |
| **Total** | **3,903.3** | **2,887.9** | **2,523.8** | **1,347.9** | **6,759.7** | **10,663.0** |

Hectares by Key and Benson (2006) severity class; 20 m pixels; a pixel belongs to the municipality that contains its centre. Full table, including unburned and no data: [`datos/superficies.csv`](datos/superficies.csv).

### Local calibration of the threshold (amendment of 2026-11-11)

The Key and Benson (2006) thresholds were not calibrated for central Chile. The EMSR715 delineation (a single category, "Burnt area") calibrates the burned / unburned cut; the limits between severity classes are still those of Key and Benson, and the table by municipality uses them.

- **2 km spatial blocks**, in pairs of similar burned fraction: 103 to fit (34,435.4 ha) and 100 to validate (28,691.9 ha), with a 200 m guard band between them. The 1,200 pixels of the M2 sample enter neither.
- **Criterion:** the threshold with the highest accuracy in area over the fit blocks; uncertainty by block bootstrap (1,000 replicates). dNBR threshold: 151 (interval 134 to 179), against 100 and 270 in Key and Benson.
- **Indices:** dNBR, RdNBR (Miller and Thode 2007) and RBR (Parks et al. 2014). Rule fixed before computing: the most accurate in validation wins unless it beats dNBR by less than 1.0 point. RBR beat it by 0.5 points (0.3 to 0.8), so dNBR stays.

Results on the validation blocks, every pixel (commission, omission and area difference against EMSR715); hectares over the whole M2 domain:

| Threshold | Value | Hectares | Accuracy | Commission | Omission | Area against EMSR715 |
|---|---:|---:|---:|---:|---:|---:|
| **dNBR calibrated** | **151** | **9,211.9 ha** | **96.4 %** | **18.5 %** | **10.7 %** | **9.5 %** |
| RdNBR calibrated | 587 | 8,739.0 ha | 96.3 % | 17.0 % | 14.5 % | 3.1 % |
| RBR calibrated | 156 | 8,960.7 ha | 96.9 % | 15.6 % | 10.2 % | 6.3 % |
| Moderate-low or higher (original rule) | dNBR 270 | 6,759.7 ha | 95.6 % | 12.2 % | 27.3 % | -17.2 % |
| All burned classes (original rule) | dNBR 100 | 10,663.0 ha | 95.5 % | 25.7 % | 6.2 % | 26.2 % |

95 % bootstrap intervals of the chosen threshold: accuracy 94.8 to 97.7 %, area difference 0.0 to 20.8 %.

**Why the headline changed.** The decision was taken on 2026-11-11, **after** seeing these results, which is why it is declared. On blocks that took no part in the fit, the original rule underestimates the area by 17.2 % and the calibrated threshold overestimates it by 9.5 %; for a figure in hectares that bias matters more than overall accuracy, which rises little (95.6 % to 96.4 %).

**Limit in the urban interface.** Of 145 damaged buildings from EMSN188 in validation blocks (at least 100 m apart; only 145 of the planned 300 fitted), the calibrated threshold puts 35.9 % in burned (Wilson 95 %: 28.5 to 43.9), against 56.6 % with all burned classes: it was calibrated on vegetation. Full detail in [`datos/ajuste_umbrales.json`](datos/ajuste_umbrales.json).

### Validation of the original rule (fixed before measuring)

Two references, two faces of the error. The protocol and the rule that picked the headline figure were written down **before** any result was seen. Copernicus EMSR715 delineated 8,600.5 ha, inside the range of that rule.

| | Reference | What it measures | Result |
|---|---|---|---|
| **M1** | Copernicus EMSN188, building damage points (Pléiades Neo, 0.3 m) | Omission in the urban interface | 54.7 % of a sample of 300 damaged buildings fall in any burned class (Wilson 95 %: 49.0 to 60.2); 14.0 % at moderate-low or higher |
| **M2** | Copernicus EMSR715, burned area delineation v2 (WorldView-2, 2 m) | Burned / unburned agreement | see below |

M2: sample of 300 points per predicted stratum, at least 200 m apart, seed 20240202; accuracy and omission weighted by stratum area (Olofsson et al. 2014).

| Threshold | Predicted burned | Area-weighted accuracy | Commission (Wilson 95 %) | Area-weighted omission |
|---|---:|---:|---:|---:|
| Moderate-low or higher | 6,759.7 ha | **96.8 %** | 11.7 % (8.5 to 15.8) | 19.8 % |
| All burned classes | 10,663.0 ha | 95.2 % | 25.7 % (21.1 to 30.9) | 7.0 % |

**Original headline rule, fixed beforehand:** publish the threshold with the higher area-weighted accuracy and the other as the range; if they differ by less than 1.0 point, publish all burned classes. Moderate-low or higher won by 1.6 points. Every number is reproduced by [`notebooks/02_validacion.ipynb`](notebooks/02_validacion.ipynb) and stored in [`datos/metricas.json`](datos/metricas.json).
<!-- end of results -->

## What the map and the factsheet show

**Interactive map** ([`docs/`](docs/), published with GitHub Pages): severity (click a pixel to see its class, hide classes from the legend), the 2021 built-up outline, GeoNames cities, and the two scenes in true colour with a before and after control. OpenStreetMap background; MapLibre GL JS is bundled in `docs/vendor/maplibre-gl/`, with no CDN. Every figure on the page comes from a `const DATA` block that `python -m severidad verify` checks against `datos/`.

**Technical factsheet** ([`ficha/factsheet.pdf`](ficha/factsheet.pdf) and [`ficha/ficha.pdf`](ficha/ficha.pdf)): map with relief, water, cities and 2021 built-up area; table per municipality; validation; method, limitations O1 to O5 and references.

## Method

1. **Scenes.** Sentinel-2 L2A `S2B_19HBD_20240118_0_L2A` (pre-fire) and `S2A_19HBD_20240212_0_L2A` (post-fire), from the Earth Search STAC catalogue, no account needed. Each was the scene with the least cloud inside the study area in its window, a rule fixed before looking at results. Bands B8A (NIR), B12 (SWIR) and the scene classification, all at 20 m.
2. **Reflectance.** The +1000 digital-number offset is **measured on water pixels** instead of taken from the catalogue (see O4).
3. **Quality mask.** No data, saturated, dark, shadow, cloud, cirrus, water and snow pixels are removed from the pre-fire scene; on the post-fire scene dark pixels and shadows are kept, because burn scars are dark.
4. **dNBR with offset.** NBR = (NIR − SWIR) / (NIR + SWIR); dNBR = NBR pre − NBR post. The mean dNBR of an unburned ring between 500 m and 2 km around the preliminary burned area (raw dNBR ≥ 0.1) is subtracted, so that differences between dates that are not fire do not count as fire (Parks et al. 2018).
5. **Classes.** Key and Benson (2006), dNBR × 1000: enhanced regrowth below −100, unburned −100 to 100, low 100 to 270, moderate-low 270 to 440, moderate-high 440 to 660, high 660 or more.
6. **Reproducibility.** Notebook 01 hashes the content of the classes raster (data type, shape, CRS, transform and pixels) and compares it with [`datos/huellas.json`](datos/huellas.json). The hash is identical on Windows and Linux.

## Limitations

- **O1. It measures vegetation, not buildings.** A large share of the buildings that Copernicus mapped as damaged fall outside every burned class (M1). NBR responds to the loss of vegetation; an urban pixel changes little even when the houses burn. Building damage needs very high resolution imagery.
- **O2. The low-severity class mostly adds commission.** Including it raises commission; leaving it out raises omission (M2 table). No Key and Benson threshold reproduces the reference; hence the burned / unburned cut was calibrated locally (see above) and the original rule stays as a range.
- **O3. The class limits are not calibrated for central Chile.** Key and Benson defined them for North American ecosystems, and no field severity plots were available here. Only the burned / unburned cut was calibrated, against EMSR715; the limits between low, moderate and high are still uncalibrated.
- **O4. Do not trust the catalogue offset blindly.** The Earth Search `sentinel-2-l2a` COGs of these scenes do not carry the +1000 offset that their STAC metadata declares. Applying it roughly doubles the burned area. The offset is therefore measured on water, where SWIR reflectance is close to zero.
- **O5. The built-up area is from 2021.** The outline (ESA WorldCover 2021 v200, class 50) is drawn only to read the map; no figure uses it. It misses later settlements and can under-represent scattered self-built housing.
- **Scope of the validation.** M1 covers only the EMSN188 area of interest in Viña del Mar. M2 covers the part of the study area inside the WorldView-2 footprint: all of Viña del Mar and Villa Alemana, and part of Valparaíso and Quilpué.
- **Independence of the reference.** EMSR715's post-event image is not one of the scenes used here, but its pre-event reference was also Sentinel-2, so the reference is mostly, not fully, independent.

## Reproduce it

Python 3.12. Downloads go to `datos_crudos/` (ignored by git): about 210 MB of Sentinel-2 bands for the study and about 600 MB more for the true-colour scenes of the map.

1. Clone the repository and enter it:
   ```bash
   git clone https://github.com/Nadir-Observatorio/incendios-severidad.git
   cd incendios-severidad
   ```
2. Create the environment and install the pinned versions:
   ```bash
   python -m venv .venv
   # Windows: .venv\Scripts\activate    Linux and macOS: source .venv/bin/activate
   pip install -r requirements.txt -e ".[dev]"    # or: conda env create -f environment.yml
   ```
3. Check that everything is in order, without network:
   ```bash
   python -m pytest -q                            # the whole test suite
   python -m severidad verify                     # README and map figures against datos/
   python herramientas/escribir_readme.py --comprobar   # results block up to date
   ```
4. Rerun the whole study from scratch, to a new folder, and compare it with what is published. The two Copernicus references must first be in `datos_crudos/referencias/` (notebook 02 explains where to get them):
   ```bash
   python -m severidad run --out datos_crudos/run1
   python -m severidad verify --out datos_crudos/run1
   ```
   `run` downloads what is missing (scenes and municipal boundaries), computes the map and both validations and writes `clases.tif`, `superficies.csv`, `huellas.json` and `metricas.json`; it never overwrites. `verify --out` compares the raster by content hash, the CSV and the fingerprints byte by byte and the metrics by content.
5. Optional, step by step in notebooks: `jupyter lab`, then `notebooks/01_severidad.ipynb` (prints `IDENTICAL to the published result` when its hash matches) and `notebooks/02_validacion.ipynb` (prints `IDENTICAL to the published metrics`).
6. Optional, reading aids:
   - Context layers: `python -m severidad.contexto descargar`, then `python -m severidad.contexto derivar` (relief, water, built-up area, cities and Chile in `datos/contexto/`, with hashes; never overwrites). No figure of the study is computed with them.
   - Factsheet: `python ficha/make_factsheet.py --force` rebuilds both PDFs and prints the content hash of each, the same on Windows and Linux.
   - Map: `python -m severidad download` downloads the true colour of both scenes and `python visor/make_viewer.py --force` rebuilds `docs/index.html` and `docs/capas/`. To view it locally: `python -m http.server 8000 --directory docs` and open http://127.0.0.1:8000/ (opened straight from disk, the browser blocks the click query).
   - README results: `python herramientas/escribir_readme.py` rewrites the results block of both README files from `datos/`.

Set `INCENDIO_DATOS` to use another folder for the notebook downloads. Code, notebooks and their messages are in English.

```text
src/severidad/   estudio.py (study parameters and steps), fuente.py (downloads), calculo.py (severity),
                 validacion.py (M1, M2), web.py (map image), contexto.py (context layers),
                 __main__.py (python -m severidad)
tests/           test suite
notebooks/       01_severidad.ipynb, 02_validacion.ipynb
datos/           clases.tif, superficies.csv, huellas.json, metricas.json; contexto/
docs/            interactive map: index.html, clases.png, capas/, vendor/maplibre-gl/
visor/           make_viewer.py and plantilla.html (build docs/index.html)
ficha/           factsheet.pdf, ficha.pdf and make_factsheet.py
herramientas/    escribir_readme.py (results block of the README files)
```

## Sources, attribution and licences

| Source | Use | Attribution | Licence |
|---|---|---|---|
| Copernicus Sentinel-2 L2A, scenes `S2B_19HBD_20240118_0_L2A` and `S2A_19HBD_20240212_0_L2A` (Earth Search catalogue) | Severity map, water and true colour of the map | Contains modified Copernicus Sentinel data (2024) | Copernicus Sentinel data: free, full and open access, with attribution |
| Copernicus EMS, EMSR715 (delineation v2) and EMSN188 (building damage) | Validation metrics only; not redistributed | Copernicus Emergency Management Service (© 2024 European Union), [EMSR715] and [EMSN188] | Copernicus EMS terms: free use, informing of the source |
| Copernicus DEM GLO-30, tiles `S33_00_W072_00` and `S34_00_W072_00` | Factsheet relief (context only) | "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved" | Copernicus WorldDEM-30 free licence |
| ESA WorldCover 10 m 2021 v200, class 50 (doi:10.5281/zenodo.7254221) | Built-up outline (context only) | "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium" | CC BY 4.0 |
| GeoNames, extract of Chile | Names and positions of cities of 50,000 inhabitants or more | GeoNames (geonames.org) | CC BY 4.0 |
| Natural Earth, 1:50m countries | Factsheet locator map | Made with Natural Earth | Public domain |
| Chilean Ministry of Public Works, public SERVICIO_DPA service | Only to sum areas per municipality; the boundaries are not drawn nor redistributed | Ministerio de Obras Públicas de Chile, SERVICIO_DPA | Not redistributed |
| OpenStreetMap | Interactive map background only; not redistributed | © OpenStreetMap contributors | ODbL |
| MapLibre GL JS, copied unchanged to `docs/vendor/maplibre-gl/` | Interactive map engine | MapLibre contributors | BSD 3-Clause |

- **Code** (including the map page): [MIT](LICENSE).
- **Derived data** (`datos/`, `docs/clases.png`, `docs/capas/` and the factsheets): CC BY 4.0, attributed as "Camilo López Barra, geographer. Burn severity of the Viña del Mar fire, February 2024. Contains modified Copernicus Sentinel data (2024)."
- Details of every source, full notices and third-party code: [LICENSE-DATA.md](LICENSE-DATA.md).
- Independent analysis, not reviewed nor endorsed by any of the data providers.

## How to cite

López Barra, C. (2026). *Burn severity of the Viña del Mar fire, February 2024: reproducible dNBR mapping with Sentinel-2 and validation against Copernicus EMS* [code and data]. https://github.com/Nadir-Observatorio/incendios-severidad

Citation metadata are in [CITATION.cff](CITATION.cff); GitHub offers them under "Cite this repository".

## References

- Key, C. H., and Benson, N. C. (2006). Landscape assessment (LA): sampling and analysis methods. In *FIREMON: Fire effects monitoring and inventory system*. USDA Forest Service, RMRS-GTR-164-CD.
- Olofsson, P., Foody, G. M., Herold, M., Stehman, S. V., Woodcock, C. E., and Wulder, M. A. (2014). Good practices for estimating area and assessing accuracy of land change. *Remote Sensing of Environment*, 148, 42-57.
- Parks, S. A., Holsinger, L. M., Voss, M. A., Loehman, R. A., and Robinson, N. P. (2018). Mean composite fire severity metrics computed with Google Earth Engine offer improved accuracy and expanded mapping potential. *Remote Sensing*, 10(6), 879.
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *Journal of the American Statistical Association*, 22(158), 209-212.
- Zanaga, D., et al. (2022). ESA WorldCover 10 m 2021 v200. Zenodo. doi:10.5281/zenodo.7254221.

---

Made by Camilo López Barra, geographer.
