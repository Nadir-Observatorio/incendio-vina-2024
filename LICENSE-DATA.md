# Data licence and attributions

The code of this repository is under the MIT licence (`LICENSE`). This file covers the data.

## Derived data in `datos/` and `docs/`

`datos/clases.tif`, `datos/superficies.csv`, `datos/huellas.json`, `datos/metricas.json`, the context layers in `datos/contexto/`, the map image `docs/clases.png`, the web map layers in `docs/capas/` and the factsheets `ficha/factsheet.pdf` (English) and `ficha/ficha.pdf` (Spanish) are released under the
[Creative Commons Attribution 4.0 International licence (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

Attribution: **Camilo López Barra, geographer. Burn severity of the Viña del Mar fire, February 2024.
Contains modified Copernicus Sentinel data 2024.**

The derived data come from:

| Source | Use in this repository | Attribution |
|---|---|---|
| Copernicus Sentinel-2 L2A scenes `S2B_19HBD_20240118_0_L2A` and `S2A_19HBD_20240212_0_L2A`, via the Earth Search STAC catalogue | NIR, SWIR and scene classification bands for the severity map (`clases.tif`, `superficies.csv`, `huellas.json`, `docs/clases.png`); true-colour `visual` asset for the before and after images of the web map (`docs/capas/antes.jpg`, `docs/capas/despues.jpg`) | Contains modified Copernicus Sentinel data 2024 |
| Municipal boundaries of the Chilean Ministry of Public Works (MOP), public `SERVICIO_DPA` service | Used only to sum the burned area per municipality (`superficies.csv`, the tables of the README files and the factsheets) and to delimit the study area. The boundaries themselves are not drawn on the maps nor redistributed | Ministerio de Obras Públicas de Chile, SERVICIO_DPA |
| Copernicus DEM GLO-30, tiles `S33_00_W072_00` and `S34_00_W072_00`, via AWS Open Data | Hillshade under the factsheet map (`datos/contexto/relieve.tif`); context only, not used by the severity result | "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved" |
| ESA WorldCover 10 m 2021 v200 ([doi:10.5281/zenodo.7254221](https://doi.org/10.5281/zenodo.7254221)), tiles `S36W072` and `S33W072`, via AWS Open Data | Built-up outline (class 50: buildings, roads and other structures) on the maps (`datos/contexto/construido.tif`, resampled to the 20 m grid, and its outline `docs/capas/construido.png`); context only, no figure is computed with it | "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium", [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Natural Earth, 1:50m admin 0 countries | Locator map (`datos/contexto/chile.geojson`) | Made with Natural Earth (public domain) |
| GeoNames, extract of Chile (`CL.zip`), populated places of 50,000 inhabitants or more inside the window | City names and positions on the maps (`datos/contexto/lugares.geojson`) | GeoNames (geonames.org), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Sentinel-2 scene classification of the two scenes above | Sea and inland water on the maps (`datos/contexto/agua.tif`) | Contains modified Copernicus Sentinel data 2024 |
| Copernicus Emergency Management Service, EMSR715 and EMSN188 | Validation metrics only (`metricas.json`) | See below |

## Validation references (not redistributed)

The Copernicus EMS products used for validation are **not included** in this repository. Notebook
`02_validacion.ipynb` explains where to download them and checks that they are the same files that
produced the published metrics. Only the metrics derived from them are published.

- Copernicus Emergency Management Service (© 2024 European Union), [EMSR715] Valparaiso (AOI01): Delineation Map, version 2.
- Copernicus Emergency Management Service (© 2024 European Union), [EMSN188] Post-wildfire damage assessment in Valparaiso region, Chile: P08 Detailed damage assessment.

Copernicus EMS On-Demand Mapping data may be reproduced, distributed, adapted and combined with other
data free of charge, provided that recipients are informed of the source; the data is provided
"as is", without warranty
([CEMS terms and conditions](https://mapping.emergency.copernicus.eu/terms-and-conditions/),
[citation guidelines](https://mapping.emergency.copernicus.eu/about/citation-guidelines/)).

## No warranty and no endorsement

The derived data are provided "as is", without warranty of any kind. They are an independent
analysis: neither the European Union, the European Space Agency, Copernicus nor the Chilean
government have reviewed or endorsed them. The burn severity classes describe change in
vegetation; they do not measure damage to buildings (see README, limitation O1).

## Context layers

The layers in `datos/contexto/` only help to read the map; they are not part of the severity
result. `python -m severidad.contexto` downloads their sources and derives them, and
`datos/contexto/procedencia.json` lists the sha256 of every source and output (content hash for the
rasters, the same on Windows and Linux).

The hillshade is an adaptation of the Copernicus DEM GLO-30, used under the free licence for the
Copernicus WorldDEM-30 ([licence text](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM/resources/license/License-COPDEM-30.pdf)),
which asks for the notice in the table above and for this sentence: "The organisations in charge of
the Copernicus programme by law or by delegation do not incur any liability for any use of the
Copernicus WorldDEM-30."

## Third-party code in `docs/`

The web map uses MapLibre GL JS 5.24.0, copied unchanged to `docs/vendor/maplibre-gl/` so the page
needs no external server for it. It is under the BSD 3-Clause licence, with the notices of the
code it bundles, all in `docs/vendor/maplibre-gl/LICENSE.txt`. The base map tiles are loaded from OpenStreetMap
(© OpenStreetMap contributors, ODbL) and are not redistributed here.
