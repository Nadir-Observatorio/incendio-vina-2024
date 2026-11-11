"""Write the results block of README.md (Spanish) and README.en.md (English) from datos/.

Every figure of the block is read from ``datos/ajuste_umbrales.json`` (calibrated headline, since
2026-11-11), ``datos/metricas.json`` (the rule fixed before measuring), ``datos/superficies.csv``
and the protocol constants of ``severidad.estudio``; nothing is typed by hand. The rest of each
README is plain text without results. ``python -m severidad verify`` and the test suite check the
block again on every push.

    python herramientas/escribir_readme.py               # rewrite the block in both README files
    python herramientas/escribir_readme.py --comprobar   # exit 1 if a README is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from severidad import calculo, estudio

ROOT = Path(__file__).resolve().parents[1]
START = {
    True: "<!-- resultados: los escribe herramientas/escribir_readme.py desde datos/; "
          "no se editan a mano -->",
    False: "<!-- results: written by herramientas/escribir_readme.py from datos/; "
           "do not edit by hand -->",
}
END = {True: "<!-- fin de resultados -->", False: "<!-- end of results -->"}
FILES = {True: "README.md", False: "README.en.md"}
BURNED_CODES = (4, 5, 6, 7)


def number(value: float, spanish: bool) -> str:
    """One decimal with thousands separators, as ``verify`` expects."""
    text = f"{value:,.1f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".") if spanish else text


def count(value: int, spanish: bool) -> str:
    """Whole number with thousands separators."""
    text = f"{value:,}"
    return text.replace(",", ".") if spanish else text


def rate(value: float, spanish: bool) -> str:
    """A percentage exactly as stored in metricas.json, as ``verify`` expects."""
    text = f"{value}"
    return text.replace(".", ",") if spanish else text


def interval(bounds: list[float], spanish: bool) -> str:
    joiner = " a " if spanish else " to "
    return rate(bounds[0], spanish) + joiner + rate(bounds[1], spanish)


def area_rows(table: dict[str, dict[int, float]], spanish: bool) -> list[str]:
    """Rows of the table per municipality, in the format ``verify`` checks."""
    head = ("| Comuna | Baja | Moderada baja | Moderada alta | Alta | Moderada baja o más | "
            "Todas las clases quemadas |" if spanish else
            "| Municipality | Low | Moderate-low | Moderate-high | High | Moderate-low or higher "
            "| All burned classes |")
    rows = [head, "|---|---:|---:|---:|---:|---:|---:|"]
    totals = [0.0] * 6
    for name, areas in table.items():
        low, mod_low, mod_high, high = (areas[code] for code in BURNED_CODES)
        values = [low, mod_low, mod_high, high, mod_low + mod_high + high,
                  low + mod_low + mod_high + high]
        totals = [t + v for t, v in zip(totals, values, strict=True)]
        rows.append(f"| {name} | " + " | ".join(number(v, spanish) for v in values) + " |")
    rows.append("| **Total** | " + " | ".join(f"**{number(v, spanish)}**" for v in totals)
                + " |")
    return rows


def calibration(adj: dict[str, Any], metrics: dict[str, Any],
                spanish: bool) -> tuple[list[str], list[str]]:
    """Headline paragraph and the section on the local calibration (amendment of 2026-11-11)."""
    s = spanish
    c, d, e = adj["chosen"], adj["domain"], adj["emsn188"]
    if c["index"] != "dNBR":
        sys.exit("The calibrated index is no longer dNBR: rewrite the wording of this script.")
    head, m2 = metrics["headline"], metrics["measurement_2"]
    reference = m2["reference_burned_ha"]
    v = adj["indices"][c["index"]]["validation"]
    pub = adj["published_thresholds_on_validation"]
    others = [n for n in adj["indices"] if n != c["index"]]
    best_other = max(others, key=lambda n: adj["indices"][n]["validation"]["accuracy_pct"])
    gap = adj["accuracy_differences_points"][f"{best_other}_minus_{c['index']}"]
    ec, ea = e[c["index"]], e["A"]
    date = adj["date"]
    day = "-".join(reversed(date.split("-")))
    # dNBR x 1000 at which each published threshold starts (lower limit of its first class).
    lower = {code: low for code, _name, low, _high in calculo.CLASSES}
    cut = {key: int(lower[code]) for key, code in estudio.THRESHOLDS.items()}
    names = {"es": {"A": "Todas las clases quemadas (regla original)",
                    "B": "Moderada baja o más (regla original)"},
             "en": {"A": "All burned classes (original rule)",
                    "B": "Moderate-low or higher (original rule)"}}["es" if s else "en"]

    def row(label: str, threshold: str, ha: float, val: dict[str, Any], bold: bool) -> str:
        cells = [label, threshold, f"{number(ha, s)} ha", f"{rate(val['accuracy_pct'], s)} %",
                 f"{rate(val['commission_pct'], s)} %", f"{rate(val['omission_pct'], s)} %",
                 f"{rate(val['area_difference_pct'], s)} %"]
        if bold:
            cells = [f"**{x}**" for x in cells]
        return "| " + " | ".join(cells) + " |"

    word = "calibrado" if s else "calibrated"
    rows = [row(f"{n} {word}", str(adj["indices"][n]["threshold"]),
                adj["indices"][n]["hectares"], adj["indices"][n]["validation"], n == c["index"])
            for n in adj["indices"]]
    rows += [row(names[k], f"dNBR {cut[k]}",
                 m2[k]["predicted_burned_ha"], pub[k], False) for k in ("B", "A")]
    if s:
        head_lines = [
            f"**{number(c['hectares'], s)} ha quemadas** según el umbral dNBR calibrado "
            f"localmente con Copernicus EMSR715 ({c['threshold']}; intervalo bootstrap del 95 %: "
            f"{number(c['hectares_interval'][0], s)} a {number(c['hectares_interval'][1], s)} "
            f"ha). EMSR715 delimitó {number(reference, s)} ha en la misma área.", "",
            f"La regla fijada antes de medir daba {number(head['hectares'], s)} ha con severidad "
            f"moderada baja o mayor, en un rango de hasta {number(head['range_up_to_hectares'], s)}"
            f" ha si se incluye la severidad baja. Se conserva como registro en "
            "[`datos/metricas.json`](datos/metricas.json); el cambio de cifra principal se "
            f"explica abajo (enmienda del {day}).",
        ]
        section = [
            f"### Calibración local del umbral (enmienda del {day})", "",
            "Los umbrales de Key y Benson (2006) no se calibraron en Chile central. Con la "
            "delimitación de EMSR715 (una sola categoría, \"Burnt area\") se calibró el corte "
            "quemado / no quemado; los límites entre clases de severidad siguen siendo los de Key "
            "y Benson, y la tabla por comuna los usa.", "",
            f"- **Bloques espaciales de {adj['protocol']['block_px'] * 20 // 1000} km**, en pares "
            f"de fracción quemada parecida: {d['fit_blocks']} para ajustar "
            f"({number(d['fit_ha'], s)} ha) y {d['validation_blocks']} para validar "
            f"({number(d['validation_ha'], s)} ha), con una franja de "
            f"{estudio.M2_MIN_DISTANCE_PX * 20} m entre ambos. Los "
            f"{count(d['m2_pixels_excluded'], s)} píxeles de la muestra de M2 no entran a "
            "ninguno.",
            "- **Criterio:** el umbral con mayor exactitud en área sobre los bloques de ajuste; "
            f"incertidumbre por bootstrap de bloques ({count(adj['protocol']['replicates'], s)} "
            "réplicas). "
            f"Umbral dNBR: {c['threshold']} (intervalo {c['threshold_interval'][0]} a "
            f"{c['threshold_interval'][1]}), frente a {cut['A']} y {cut['B']} de Key y Benson.",
            "- **Índices:** dNBR, RdNBR (Miller y Thode 2007) y RBR (Parks et al. 2014). Regla "
            f"fijada antes de calcular: gana el más exacto en validación salvo que supere al dNBR "
            f"por menos de {rate(estudio.HEADLINE_TIE_POINTS, s)} punto. {best_other} lo superó "
            f"por {rate(gap[0], s)} puntos ({interval(gap[1:], s)}), así que queda dNBR.", "",
            "Resultados en los bloques de validación, con todos los píxeles (comisión, omisión y "
            "diferencia de área frente a EMSR715); hectáreas en todo el dominio de M2:", "",
            "| Umbral | Valor | Hectáreas | Exactitud | Comisión | Omisión | Área frente a "
            "EMSR715 |", "|---|---:|---:|---:|---:|---:|---:|",
            *rows, "",
            f"Intervalos bootstrap del 95 % del umbral elegido: exactitud "
            f"{interval(v['accuracy_interval'], s)} %, diferencia de área "
            f"{interval(v['area_difference_interval'], s)} %.", "",
            f"**Por qué cambió la cifra principal.** La decisión se tomó el {day}, **después** "
            "de ver estos resultados, y por eso se declara. En bloques que no participaron del "
            "ajuste, la regla original subestima el área en "
            f"{rate(-pub['B']['area_difference_pct'], s)} % y el umbral calibrado la sobreestima "
            f"en {rate(v['area_difference_pct'], s)} %; para "
            "una cifra de hectáreas, ese sesgo pesa más que la exactitud global, que sube poco "
            f"({rate(pub['B']['accuracy_pct'], s)} % a {rate(v['accuracy_pct'], s)} %).", "",
            f"**Límite en la interfaz urbana.** De {e['sample']} edificaciones dañadas de EMSN188 "
            f"en bloques de validación (separadas al menos {int(estudio.M1_MIN_DISTANCE_M)} m; "
            f"solo cabían {e['sample']} de las {e['sample_target']} previstas), el umbral "
            f"calibrado deja en quemado el {rate(ec['pct'], s)} % (Wilson 95 %: "
            f"{interval(ec['wilson'], s)}), frente a {rate(ea['pct'], s)} % con todas las clases: "
            "se calibró sobre vegetación. Detalle completo en "
            "[`datos/ajuste_umbrales.json`](datos/ajuste_umbrales.json).",
        ]
    else:
        head_lines = [
            f"**{number(c['hectares'], s)} ha burned** according to the dNBR threshold calibrated "
            f"locally against Copernicus EMSR715 ({c['threshold']}; 95 % bootstrap interval: "
            f"{number(c['hectares_interval'][0], s)} to {number(c['hectares_interval'][1], s)} "
            f"ha). EMSR715 delineated {number(reference, s)} ha in the same area.", "",
            f"The rule fixed before measuring gave {number(head['hectares'], s)} ha at "
            "moderate-low severity or higher, within a range up to "
            f"{number(head['range_up_to_hectares'], s)} ha if low severity is included. It is "
            "kept as the record in [`datos/metricas.json`](datos/metricas.json); the change of "
            f"headline is explained below (amendment of {date}).",
        ]
        section = [
            f"### Local calibration of the threshold (amendment of {date})", "",
            "The Key and Benson (2006) thresholds were not calibrated for central Chile. The "
            "EMSR715 delineation (a single category, \"Burnt area\") calibrates the burned / "
            "unburned cut; the limits between severity classes are still those of Key and "
            "Benson, and the table by municipality uses them.", "",
            f"- **{adj['protocol']['block_px'] * 20 // 1000} km spatial blocks**, in pairs of "
            f"similar burned fraction: {d['fit_blocks']} to fit ({number(d['fit_ha'], s)} ha) and "
            f"{d['validation_blocks']} to validate ({number(d['validation_ha'], s)} ha), with a "
            f"{estudio.M2_MIN_DISTANCE_PX * 20} m guard band between them. The "
            f"{count(d['m2_pixels_excluded'], s)} pixels of the M2 sample enter neither.",
            "- **Criterion:** the threshold with the highest accuracy in area over the fit "
            f"blocks; uncertainty by block bootstrap ({count(adj['protocol']['replicates'], s)} "
            f"replicates). dNBR threshold: {c['threshold']} (interval "
            f"{c['threshold_interval'][0]} to {c['threshold_interval'][1]}), against "
            f"{cut['A']} and {cut['B']} in Key and Benson.",
            "- **Indices:** dNBR, RdNBR (Miller and Thode 2007) and RBR (Parks et al. 2014). Rule "
            "fixed before computing: the most accurate in validation wins unless it beats dNBR by "
            f"less than {rate(estudio.HEADLINE_TIE_POINTS, s)} point. {best_other} beat it by "
            f"{rate(gap[0], s)} points ({interval(gap[1:], s)}), so dNBR stays.", "",
            "Results on the validation blocks, every pixel (commission, omission and area "
            "difference against EMSR715); hectares over the whole M2 domain:", "",
            "| Threshold | Value | Hectares | Accuracy | Commission | Omission | Area against "
            "EMSR715 |", "|---|---:|---:|---:|---:|---:|---:|",
            *rows, "",
            f"95 % bootstrap intervals of the chosen threshold: accuracy "
            f"{interval(v['accuracy_interval'], s)} %, area difference "
            f"{interval(v['area_difference_interval'], s)} %.", "",
            f"**Why the headline changed.** The decision was taken on {date}, **after** seeing "
            "these results, which is why it is declared. On blocks that took no part in the fit, "
            "the original rule underestimates the area by "
            f"{rate(-pub['B']['area_difference_pct'], s)} % and the calibrated threshold "
            f"overestimates it by {rate(v['area_difference_pct'], s)} %; for a figure in "
            "hectares that bias matters more than overall accuracy, which "
            f"rises little ({rate(pub['B']['accuracy_pct'], s)} % to {rate(v['accuracy_pct'], s)}"
            " %).", "",
            f"**Limit in the urban interface.** Of {e['sample']} damaged buildings from EMSN188 "
            f"in validation blocks (at least {int(estudio.M1_MIN_DISTANCE_M)} m apart; only "
            f"{e['sample']} of the planned {e['sample_target']} fitted), the calibrated threshold "
            f"puts {rate(ec['pct'], s)} % in burned (Wilson 95 %: {interval(ec['wilson'], s)}), "
            f"against {rate(ea['pct'], s)} % with all burned classes: it was calibrated on "
            "vegetation. Full detail in "
            "[`datos/ajuste_umbrales.json`](datos/ajuste_umbrales.json).",
        ]
    return head_lines, section


def block(metrics: dict[str, Any], table: dict[str, dict[int, float]], spanish: bool,
          adj: dict[str, Any]) -> str:
    """The results section of one README."""
    head, m1, m2 = metrics["headline"], metrics["measurement_1"], metrics["measurement_2"]
    if head["threshold"] != "B":
        sys.exit("The headline threshold is no longer B: rewrite the wording of this script.")
    a, b = m2["A"], m2["B"]
    reference = m2["reference_burned_ha"]
    inside = head["hectares"] <= reference <= head["range_up_to_hectares"]
    margin = round(b["weighted_accuracy_pct"] - a["weighted_accuracy_pct"], 1)
    distance_m = estudio.M2_MIN_DISTANCE_PX * 20
    s = spanish
    head_lines, section = calibration(adj, metrics, spanish)
    if s:
        lines = [
            "## Resultados", "",
            *head_lines, "",
            *area_rows(table, s), "",
            "Hectáreas por clase de severidad de Key y Benson (2006); píxeles de 20 m; un píxel "
            "pertenece a la comuna que contiene su centro. Tabla completa, con no quemado y sin "
            "dato: [`datos/superficies.csv`](datos/superficies.csv).", "",
            *section, "",
            "### Validación de la regla original (fijada antes de medir)", "",
            "Dos referencias, dos caras del error. El protocolo y la regla que elegía la cifra "
            "principal se escribieron **antes** de ver cualquier resultado. Copernicus EMSR715 "
            f"delimitó {number(reference, s)} ha, {'dentro' if inside else 'fuera'} del rango "
            "de esa regla.", "",
            "| | Referencia | Qué mide | Resultado |", "|---|---|---|---|",
            "| **M1** | Copernicus EMSN188, puntos de daño a edificaciones (Pléiades Neo, 0,3 m) "
            "| Omisión en la interfaz urbana | "
            f"{rate(m1['A']['pct'], s)} % de una muestra de {m1['A']['n']} edificaciones dañadas "
            f"cae en alguna clase quemada (Wilson 95 %: {interval(m1['A']['wilson'], s)}); "
            f"{rate(m1['B']['pct'], s)} % con moderada baja o más |",
            "| **M2** | Copernicus EMSR715, delimitación del área quemada v2 (WorldView-2, 2 m) "
            "| Concordancia quemado / no quemado | ver abajo |", "",
            f"M2: muestra de {estudio.SAMPLE_SIZE} puntos por estrato predicho, separados al "
            f"menos {distance_m} m, semilla {estudio.SEED}; exactitud y omisión ponderadas por la "
            "superficie de cada estrato (Olofsson et al. 2014).", "",
            "| Umbral | Quemado predicho | Exactitud ponderada | Comisión (Wilson 95 %) | "
            "Omisión ponderada |", "|---|---:|---:|---:|---:|",
            f"| Moderada baja o más | {number(b['predicted_burned_ha'], s)} ha | "
            f"**{rate(b['weighted_accuracy_pct'], s)} %** | {rate(b['commission_pct'], s)} % "
            f"({interval(b['commission_wilson'], s)}) | {rate(b['weighted_omission_pct'], s)} % |",
            f"| Todas las clases quemadas | {number(a['predicted_burned_ha'], s)} ha | "
            f"{rate(a['weighted_accuracy_pct'], s)} % | {rate(a['commission_pct'], s)} % "
            f"({interval(a['commission_wilson'], s)}) | {rate(a['weighted_omission_pct'], s)} % |",
            "",
            "**Regla original de la cifra principal, fijada de antemano:** se publica el umbral "
            "con mayor exactitud ponderada y el otro como rango; si difieren en menos de "
            f"{rate(estudio.HEADLINE_TIE_POINTS, s)} punto, se publica el de todas las clases. "
            f"Ganó moderada baja o más, por {rate(margin, s)} puntos. Todas las cifras las "
            "reproduce [`notebooks/02_validacion.ipynb`](notebooks/02_validacion.ipynb) y "
            "quedan en [`datos/metricas.json`](datos/metricas.json).",
        ]
    else:
        lines = [
            "## Results", "",
            *head_lines, "",
            *area_rows(table, s), "",
            "Hectares by Key and Benson (2006) severity class; 20 m pixels; a pixel belongs to "
            "the municipality that contains its centre. Full table, including unburned and no "
            "data: [`datos/superficies.csv`](datos/superficies.csv).", "",
            *section, "",
            "### Validation of the original rule (fixed before measuring)", "",
            "Two references, two faces of the error. The protocol and the rule that picked the "
            "headline figure were written down **before** any result was seen. Copernicus "
            f"EMSR715 delineated {number(reference, s)} ha, {'inside' if inside else 'outside'} "
            "the range of that rule.", "",
            "| | Reference | What it measures | Result |", "|---|---|---|---|",
            "| **M1** | Copernicus EMSN188, building damage points (Pléiades Neo, 0.3 m) "
            "| Omission in the urban interface | "
            f"{rate(m1['A']['pct'], s)} % of a sample of {m1['A']['n']} damaged buildings fall in "
            f"any burned class (Wilson 95 %: {interval(m1['A']['wilson'], s)}); "
            f"{rate(m1['B']['pct'], s)} % at moderate-low or higher |",
            "| **M2** | Copernicus EMSR715, burned area delineation v2 (WorldView-2, 2 m) "
            "| Burned / unburned agreement | see below |", "",
            f"M2: sample of {estudio.SAMPLE_SIZE} points per predicted stratum, at least "
            f"{distance_m} m apart, seed {estudio.SEED}; accuracy and omission weighted by "
            "stratum area (Olofsson et al. 2014).", "",
            "| Threshold | Predicted burned | Area-weighted accuracy | Commission (Wilson 95 %) "
            "| Area-weighted omission |", "|---|---:|---:|---:|---:|",
            f"| Moderate-low or higher | {number(b['predicted_burned_ha'], s)} ha | "
            f"**{rate(b['weighted_accuracy_pct'], s)} %** | {rate(b['commission_pct'], s)} % "
            f"({interval(b['commission_wilson'], s)}) | {rate(b['weighted_omission_pct'], s)} % |",
            f"| All burned classes | {number(a['predicted_burned_ha'], s)} ha | "
            f"{rate(a['weighted_accuracy_pct'], s)} % | {rate(a['commission_pct'], s)} % "
            f"({interval(a['commission_wilson'], s)}) | {rate(a['weighted_omission_pct'], s)} % |",
            "",
            "**Original headline rule, fixed beforehand:** publish the threshold with the higher "
            "area-weighted accuracy and the other as the range; if they differ by less than "
            f"{rate(estudio.HEADLINE_TIE_POINTS, s)} point, publish all burned classes. "
            f"Moderate-low or higher won by {rate(margin, s)} points. Every number is reproduced "
            "by [`notebooks/02_validacion.ipynb`](notebooks/02_validacion.ipynb) and stored in "
            "[`datos/metricas.json`](datos/metricas.json).",
        ]
    return "\n".join([START[spanish], *lines, END[spanish]])


def updated(text: str, new_block: str, spanish: bool) -> str:
    """``text`` with its results block replaced; fails if the markers are missing."""
    start, end = text.find(START[spanish]), text.find(END[spanish])
    if start < 0 or end < start:
        sys.exit(f"{FILES[spanish]}: results markers not found")
    return text[:start] + new_block + text[end + len(END[spanish]):]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--comprobar", action="store_true",
                        help="do not write; exit 1 if a README is out of date")
    parser.add_argument("--raiz", type=Path, default=ROOT, help="repository folder")
    args = parser.parse_args(argv)
    data = args.raiz / "datos"
    metrics = json.loads((data / estudio.METRICS_FILE).read_text(encoding="utf-8"))
    table = estudio.read_areas(data / estudio.AREAS_FILE)
    adj = json.loads((data / estudio.ADJUSTMENT_FILE).read_text(encoding="utf-8"))
    stale = []
    for spanish, name in FILES.items():
        path = args.raiz / name
        text = path.read_text(encoding="utf-8")
        new = updated(text, block(metrics, table, spanish, adj), spanish)
        if new == text:
            continue
        stale.append(name)
        if not args.comprobar:
            path.write_bytes(new.encode("utf-8"))
    if args.comprobar and stale:
        print("Out of date: " + ", ".join(stale))
        return 1
    print(("Out of date: " if args.comprobar else "Written: ") + ", ".join(stale)
          if stale else "README files up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
