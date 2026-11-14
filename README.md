*Read in English: [README.en.md](README.en.md)*

# Severidad del incendio de Viña del Mar, febrero de 2024

¿Cuánta superficie se quemó, y con qué severidad, en el incendio del 2 y 3 de febrero de 2024 en Valparaíso, Viña del Mar, Quilpué y Villa Alemana? Este repositorio lo responde con el dNBR entre dos escenas Sentinel-2, del 18 de enero y del 12 de febrero de 2024. **Rehace el mapa desde esas escenas y `python -m severidad verify` comprueba que su huella de contenido sea la de `datos/clases.tif`; rehecho en Windows y en Linux, da la misma.** Lo valida contra la delimitación EMSR715 (WorldView-2, 2 m) y los puntos de daño a edificaciones de EMSN188, ambos del Servicio de Gestión de Emergencias de Copernicus.

- **Mapa interactivo:** [nadir-observatorio.github.io/incendios-severidad](https://nadir-observatorio.github.io/incendios-severidad/)
- **Ficha técnica (PDF, dos páginas A4):** [español](ficha/ficha.pdf) · [inglés](ficha/factsheet.pdf)
- **Fuentes y método:** [nadir-observatorio.github.io/metodo.html](https://nadir-observatorio.github.io/metodo.html)
- **Caso hermano, daño a edificaciones con radar Sentinel-1:** [github.com/Nadir-Observatorio/incendios-dano-edificaciones](https://github.com/Nadir-Observatorio/incendios-dano-edificaciones)

<!-- resultados: los escribe herramientas/escribir_readme.py desde datos/; no se editan a mano -->
## Resultados

**9.211,9 ha quemadas** según el umbral dNBR calibrado localmente con Copernicus EMSR715 (151; intervalo bootstrap del 95 %: 8.634,5 a 9.582,1 ha). EMSR715 delimitó 8.600,5 ha en la misma área.

La regla fijada antes de medir daba 6.759,7 ha con severidad moderada baja o mayor, en un rango de hasta 10.663,0 ha si se incluye la severidad baja. Se conserva como registro en [`datos/metricas.json`](datos/metricas.json); el cambio de cifra principal se explica abajo (enmienda del 11-11-2026).

| Comuna | Baja | Moderada baja | Moderada alta | Alta | Moderada baja o más | Todas las clases quemadas |
|---|---:|---:|---:|---:|---:|---:|
| Valparaíso | 1.656,2 | 1.154,4 | 798,0 | 330,8 | 2.283,2 | 3.939,4 |
| Viña del Mar | 865,7 | 815,9 | 990,4 | 589,1 | 2.395,4 | 3.261,1 |
| Quilpué | 651,0 | 556,1 | 595,6 | 371,0 | 1.522,6 | 2.173,6 |
| Villa Alemana | 730,4 | 361,5 | 140,0 | 57,0 | 558,4 | 1.288,9 |
| **Total** | **3.903,3** | **2.887,9** | **2.523,8** | **1.347,9** | **6.759,7** | **10.663,0** |

Hectáreas por clase de severidad de Key y Benson (2006); píxeles de 20 m; un píxel pertenece a la comuna que contiene su centro. Tabla completa, con no quemado y sin dato: [`datos/superficies.csv`](datos/superficies.csv).

### Calibración local del umbral (enmienda del 11-11-2026)

Los umbrales de Key y Benson (2006) no se calibraron en Chile central. Con la delimitación de EMSR715 (una sola categoría, "Burnt area") se calibró el corte quemado / no quemado; los límites entre clases de severidad siguen siendo los de Key y Benson, y la tabla por comuna los usa.

- **Bloques espaciales de 2 km**, en pares de fracción quemada parecida: 103 para ajustar (34.435,4 ha) y 100 para validar (28.691,9 ha), con una franja de 200 m entre ambos. Los 1.200 píxeles de la muestra de M2 no entran a ninguno.
- **Criterio:** el umbral con mayor exactitud en área sobre los bloques de ajuste; incertidumbre por bootstrap de bloques (1.000 réplicas). Umbral dNBR: 151 (intervalo 134 a 179), frente a 100 y 270 de Key y Benson.
- **Índices:** dNBR, RdNBR (Miller y Thode 2007) y RBR (Parks et al. 2014). Regla fijada antes de calcular: gana el más exacto en validación salvo que supere al dNBR por menos de 1,0 punto. RBR lo superó por 0,5 puntos (0,3 a 0,8), así que queda dNBR.

Resultados en los bloques de validación, con todos los píxeles (comisión, omisión y diferencia de área frente a EMSR715); hectáreas en todo el dominio de M2:

| Umbral | Valor | Hectáreas | Exactitud | Comisión | Omisión | Área frente a EMSR715 |
|---|---:|---:|---:|---:|---:|---:|
| **dNBR calibrado** | **151** | **9.211,9 ha** | **96,4 %** | **18,5 %** | **10,7 %** | **9,5 %** |
| RdNBR calibrado | 587 | 8.739,0 ha | 96,3 % | 17,0 % | 14,5 % | 3,1 % |
| RBR calibrado | 156 | 8.960,7 ha | 96,9 % | 15,6 % | 10,2 % | 6,3 % |
| Moderada baja o más (regla original) | dNBR 270 | 6.759,7 ha | 95,6 % | 12,2 % | 27,3 % | -17,2 % |
| Todas las clases quemadas (regla original) | dNBR 100 | 10.663,0 ha | 95,5 % | 25,7 % | 6,2 % | 26,2 % |

Intervalos bootstrap del 95 % del umbral elegido: exactitud 94,8 a 97,7 %, diferencia de área 0,0 a 20,8 %.

**Por qué cambió la cifra principal.** La decisión se tomó el 11-11-2026, **después** de ver estos resultados, y por eso se declara. En bloques que no participaron del ajuste, la regla original subestima el área en 17,2 % y el umbral calibrado la sobreestima en 9,5 %; para una cifra de hectáreas, ese sesgo pesa más que la exactitud global, que sube poco (95,6 % a 96,4 %).

**Límite en la interfaz urbana.** De 145 edificaciones dañadas de EMSN188 en bloques de validación (separadas al menos 100 m; solo cabían 145 de las 300 previstas), el umbral calibrado deja en quemado el 35,9 % (Wilson 95 %: 28,5 a 43,9), frente a 56,6 % con todas las clases: se calibró sobre vegetación. Detalle completo en [`datos/ajuste_umbrales.json`](datos/ajuste_umbrales.json).

### Validación de la regla original (fijada antes de medir)

Dos referencias, dos caras del error. El protocolo y la regla que elegía la cifra principal se escribieron **antes** de ver cualquier resultado. Copernicus EMSR715 delimitó 8.600,5 ha, dentro del rango de esa regla.

| | Referencia | Qué mide | Resultado |
|---|---|---|---|
| **M1** | Copernicus EMSN188, puntos de daño a edificaciones (Pléiades Neo, 0,3 m) | Omisión en la interfaz urbana | 54,7 % de una muestra de 300 edificaciones dañadas cae en alguna clase quemada (Wilson 95 %: 49,0 a 60,2); 14,0 % con moderada baja o más |
| **M2** | Copernicus EMSR715, delimitación del área quemada v2 (WorldView-2, 2 m) | Concordancia quemado / no quemado | ver abajo |

M2: muestra de 300 puntos por estrato predicho, separados al menos 200 m, semilla 20240202; exactitud y omisión ponderadas por la superficie de cada estrato (Olofsson et al. 2014).

| Umbral | Quemado predicho | Exactitud ponderada | Comisión (Wilson 95 %) | Omisión ponderada |
|---|---:|---:|---:|---:|
| Moderada baja o más | 6.759,7 ha | **96,8 %** | 11,7 % (8,5 a 15,8) | 19,8 % |
| Todas las clases quemadas | 10.663,0 ha | 95,2 % | 25,7 % (21,1 a 30,9) | 7,0 % |

**Regla original de la cifra principal, fijada de antemano:** se publica el umbral con mayor exactitud ponderada y el otro como rango; si difieren en menos de 1,0 punto, se publica el de todas las clases. Ganó moderada baja o más, por 1,6 puntos. Todas las cifras las reproduce [`notebooks/02_validacion.ipynb`](notebooks/02_validacion.ipynb) y quedan en [`datos/metricas.json`](datos/metricas.json).
<!-- fin de resultados -->

## Qué hay en el mapa y en la ficha

**Mapa interactivo** ([`docs/`](docs/), publicado con GitHub Pages): severidad (con consulta de clase al hacer clic y ocultar clases desde la leyenda), contorno del área construida 2021, ciudades de GeoNames y las dos escenas en color real con un control de antes a después. Fondo de OpenStreetMap; MapLibre GL JS va dentro del repositorio (`docs/vendor/maplibre-gl/`), sin CDN. Todas las cifras de la página salen de un bloque `const DATA` que `python -m severidad verify` contrasta con `datos/`.

**Ficha técnica** ([`ficha/ficha.pdf`](ficha/ficha.pdf) y [`ficha/factsheet.pdf`](ficha/factsheet.pdf)): mapa con relieve, agua, ciudades y área construida 2021; tabla por comuna; validación; método, limitaciones O1 a O5 y referencias.

## Método

1. **Escenas.** Sentinel-2 L2A `S2B_19HBD_20240118_0_L2A` (antes del incendio) y `S2A_19HBD_20240212_0_L2A` (después), del catálogo STAC Earth Search, sin cuenta. Cada una fue la de menor nubosidad dentro del área de estudio en su ventana, regla fijada antes de mirar resultados. Bandas B8A (infrarrojo cercano), B12 (infrarrojo de onda corta) y la clasificación de escena, todas a 20 m.
2. **Reflectancia.** El desplazamiento de +1000 en los números digitales se **mide en píxeles de agua** en vez de tomarlo del catálogo (ver O4).
3. **Máscara de calidad.** En la escena previa se eliminan los píxeles sin dato, saturados, oscuros, de sombra, nube, cirros, agua y nieve; en la posterior se conservan los oscuros y las sombras, porque las cicatrices de quema son oscuras.
4. **dNBR con offset.** NBR = (NIR − SWIR) / (NIR + SWIR); dNBR = NBR previo − NBR posterior. Se resta el dNBR medio de un anillo no quemado entre 500 m y 2 km alrededor del área quemada preliminar (dNBR bruto ≥ 0,1), para que las diferencias entre fechas que no son fuego no cuenten como fuego (Parks et al. 2018).
5. **Clases.** Key y Benson (2006), dNBR × 1000: rebrote bajo −100, no quemado de −100 a 100, baja de 100 a 270, moderada baja de 270 a 440, moderada alta de 440 a 660, alta desde 660.
6. **Reproducibilidad.** El notebook 01 calcula la huella del contenido del ráster de clases (tipo de dato, forma, sistema de referencia, transformación y píxeles) y la compara con [`datos/huellas.json`](datos/huellas.json). La huella es idéntica en Windows y Linux.

## Limitaciones

- **O1. Mide vegetación, no edificaciones.** Una parte importante de las edificaciones que Copernicus marcó como dañadas cae fuera de toda clase quemada (M1). El NBR responde a la pérdida de vegetación; un píxel urbano cambia poco aunque las casas se quemen. El daño a edificaciones exige imágenes de muy alta resolución.
- **O2. La clase baja agrega sobre todo comisión.** Incluirla sube la comisión; dejarla fuera sube la omisión (tabla de M2). Ningún umbral de Key y Benson reproduce la referencia; por eso el corte quemado / no quemado se calibró localmente (ver arriba) y la regla original queda como rango.
- **O3. Los límites de las clases no están calibrados para Chile central.** Key y Benson los definieron para ecosistemas de Norteamérica, y aquí no hubo parcelas de severidad en terreno. Solo el corte quemado / no quemado se calibró, contra EMSR715; los límites entre baja, moderada y alta siguen sin calibrar.
- **O4. No confiar a ciegas en el desplazamiento del catálogo.** Los COG de `sentinel-2-l2a` de Earth Search de estas escenas no traen el +1000 que declara su metadato STAC. Aplicarlo casi duplica el área quemada. Por eso el desplazamiento se mide en el agua, donde la reflectancia del infrarrojo de onda corta es cercana a cero.
- **O5. El área construida es de 2021.** El contorno (ESA WorldCover 2021 v200, clase 50) se dibuja solo para leer el mapa; ninguna cifra lo usa. No incluye lo construido después y puede subrepresentar la autoconstrucción dispersa.
- **Alcance de la validación.** M1 cubre solo el área de interés de EMSN188, en Viña del Mar. M2 cubre la parte del área de estudio dentro de la huella de la imagen WorldView-2: todo Viña del Mar y Villa Alemana, y parte de Valparaíso y Quilpué.
- **Independencia de la referencia.** La imagen posterior de EMSR715 no es ninguna de las usadas aquí, pero su referencia previa también fue Sentinel-2, así que la referencia es mayormente, no del todo, independiente.

## Cómo reproducirlo

Python 3.12. Las descargas van a `datos_crudos/` (ignorado por git): unos 210 MB de bandas de Sentinel-2 para el estudio y unos 600 MB más para las escenas en color real del mapa.

1. Clonar el repositorio y entrar a la carpeta:
   ```bash
   git clone https://github.com/Nadir-Observatorio/incendios-severidad.git
   cd incendios-severidad
   ```
2. Crear el entorno e instalar las versiones fijadas:
   ```bash
   python -m venv .venv
   # Windows: .venv\Scripts\activate    Linux y macOS: source .venv/bin/activate
   pip install -r requirements.txt -e ".[dev]"    # o: conda env create -f environment.yml
   ```
3. Comprobar que todo está en orden, sin red:
   ```bash
   python -m pytest -q                            # todas las pruebas
   python -m severidad verify                     # cifras de los README y del mapa contra datos/
   python herramientas/escribir_readme.py --comprobar   # bloque de resultados al día
   ```
4. Rehacer el estudio completo desde cero, en una carpeta nueva, y compararlo con lo publicado. Antes hay que dejar las dos referencias de Copernicus en `datos_crudos/referencias/` (el notebook 02 explica dónde obtenerlas):
   ```bash
   python -m severidad run --out datos_crudos/corrida1
   python -m severidad verify --out datos_crudos/corrida1
   ```
   `run` descarga lo que falte (escenas y límites comunales), calcula el mapa y las dos validaciones y escribe `clases.tif`, `superficies.csv`, `huellas.json` y `metricas.json`; nunca sobrescribe. `verify --out` compara el ráster por huella de contenido, el CSV y las huellas byte a byte y las métricas por contenido.
5. Opcional, paso a paso en notebooks: `jupyter lab`, luego `notebooks/01_severidad.ipynb` (imprime `IDENTICAL to the published result` si la huella coincide) y `notebooks/02_validacion.ipynb` (imprime `IDENTICAL to the published metrics`).
6. Opcional, piezas de lectura:
   - Capas de contexto: `python -m severidad.contexto descargar` y luego `python -m severidad.contexto derivar` (relieve, agua, área construida, ciudades y Chile en `datos/contexto/`, con huellas; no sobrescribe). Ninguna cifra del estudio se calcula con ellas.
   - Ficha: `python ficha/make_factsheet.py --force` rehace los dos PDF e imprime la huella de contenido de cada uno, igual en Windows y Linux.
   - Mapa: `python -m severidad download` descarga el color real de las dos escenas y `python visor/make_viewer.py --force` rehace `docs/index.html` y `docs/capas/`. Para verlo en el computador: `python -m http.server 8000 --directory docs` y abrir http://127.0.0.1:8000/ (abierto directo desde el disco, el navegador bloquea la consulta por clic).
   - Resultados del README: `python herramientas/escribir_readme.py` reescribe el bloque de resultados de los dos README desde `datos/`.

La variable `INCENDIO_DATOS` permite usar otra carpeta para las descargas de los notebooks. El código, los notebooks y sus mensajes están en inglés.

```text
src/severidad/   estudio.py (parámetros y pasos), fuente.py (descargas), calculo.py (severidad),
                 validacion.py (M1, M2), web.py (imagen del mapa), contexto.py (capas de contexto),
                 __main__.py (python -m severidad)
tests/           pruebas
notebooks/       01_severidad.ipynb, 02_validacion.ipynb
datos/           clases.tif, superficies.csv, huellas.json, metricas.json; contexto/
docs/            mapa interactivo: index.html, clases.png, capas/, vendor/maplibre-gl/
visor/           make_viewer.py y plantilla.html (arman docs/index.html)
ficha/           ficha.pdf, factsheet.pdf y make_factsheet.py
herramientas/    escribir_readme.py (bloque de resultados de los README)
```

## Fuentes, atribución y licencias

| Fuente | Uso | Atribución | Licencia |
|---|---|---|---|
| Copernicus Sentinel-2 L2A, escenas `S2B_19HBD_20240118_0_L2A` y `S2A_19HBD_20240212_0_L2A` (catálogo Earth Search) | Mapa de severidad, agua y color real del mapa | Contiene datos modificados de Copernicus Sentinel (2024) | Datos Sentinel de Copernicus: acceso libre, pleno y abierto, con atribución |
| Copernicus EMS, EMSR715 (delimitación v2) y EMSN188 (daño a edificaciones) | Solo métricas de validación; no se redistribuyen | Copernicus Emergency Management Service (© 2024 European Union), [EMSR715] y [EMSN188] | Términos de Copernicus EMS: uso libre informando la fuente |
| Copernicus DEM GLO-30, teselas `S33_00_W072_00` y `S34_00_W072_00` | Relieve de la ficha (solo contexto) | "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved" | Licencia libre de Copernicus WorldDEM-30 |
| ESA WorldCover 10 m 2021 v200, clase 50 (doi:10.5281/zenodo.7254221) | Contorno del área construida (solo contexto) | "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium" | CC BY 4.0 |
| GeoNames, extracto de Chile | Nombres y posición de ciudades de 50.000 habitantes o más | GeoNames (geonames.org) | CC BY 4.0 |
| Natural Earth, 1:50m países | Mapa de ubicación de la ficha | Made with Natural Earth | Dominio público |
| Ministerio de Obras Públicas, servicio público SERVICIO_DPA | Solo para sumar superficies por comuna; los límites no se dibujan ni se redistribuyen | Ministerio de Obras Públicas de Chile, SERVICIO_DPA | Sin redistribución |
| OpenStreetMap | Solo fondo del mapa interactivo; no se redistribuye | © OpenStreetMap contributors | ODbL |
| MapLibre GL JS, copiado sin cambios en `docs/vendor/maplibre-gl/` | Motor del mapa interactivo | MapLibre contributors | BSD 3-Clause |

- **Código** (incluida la página del mapa): [MIT](LICENSE).
- **Datos derivados** (`datos/`, `docs/clases.png`, `docs/capas/` y las fichas): CC BY 4.0, con la atribución "Camilo López Barra, geógrafo. Severidad del incendio de Viña del Mar, febrero de 2024. Contiene datos modificados de Copernicus Sentinel (2024)."
- Detalle de cada fuente, avisos completos y código de terceros: [LICENSE-DATA.md](LICENSE-DATA.md).
- Análisis independiente, sin revisión ni respaldo de ninguno de los proveedores de datos.

## Cómo citar

López Barra, C. (2026). *Severidad del incendio de Viña del Mar, febrero de 2024: mapa reproducible con Sentinel-2 y validación contra Copernicus EMS* [código y datos]. https://github.com/Nadir-Observatorio/incendios-severidad

Los metadatos de cita están en [CITATION.cff](CITATION.cff); GitHub los ofrece en el botón "Cite this repository".

## Referencias

- Key, C. H., y Benson, N. C. (2006). Landscape assessment (LA): sampling and analysis methods. En *FIREMON: Fire effects monitoring and inventory system*. USDA Forest Service, RMRS-GTR-164-CD.
- Olofsson, P., Foody, G. M., Herold, M., Stehman, S. V., Woodcock, C. E., y Wulder, M. A. (2014). Good practices for estimating area and assessing accuracy of land change. *Remote Sensing of Environment*, 148, 42-57.
- Parks, S. A., Holsinger, L. M., Voss, M. A., Loehman, R. A., y Robinson, N. P. (2018). Mean composite fire severity metrics computed with Google Earth Engine offer improved accuracy and expanded mapping potential. *Remote Sensing*, 10(6), 879.
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *Journal of the American Statistical Association*, 22(158), 209-212.
- Zanaga, D., et al. (2022). ESA WorldCover 10 m 2021 v200. Zenodo. doi:10.5281/zenodo.7254221.

---

Hecho por Camilo López Barra, geógrafo.
