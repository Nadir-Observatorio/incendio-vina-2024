"""Write the results block of README.md (Spanish) and README.en.md (English) from datos/.

Every figure of the block is read from ``datos/metricas.json``, ``datos/superficies.csv`` and the
protocol constants of ``severidad.estudio``; nothing is typed by hand. The rest of each README is
plain text without results. ``python -m severidad verify`` and the test suite check the block
again on every push.

    python herramientas/escribir_readme.py               # rewrite the block in both README files
    python herramientas/escribir_readme.py --comprobar   # exit 1 if a README is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from severidad import estudio

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


def block(metrics: dict[str, Any], table: dict[str, dict[int, float]], spanish: bool) -> str:
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
    if s:
        lines = [
            "## Resultados", "",
            f"**{number(head['hectares'], s)} ha se quemaron con severidad moderada baja o "
            f"mayor**, dentro de un rango que llega a {number(head['range_up_to_hectares'], s)} "
            "ha si se incluye la severidad baja. Copernicus EMSR715 delimitó "
            f"{number(reference, s)} ha en la misma área, "
            f"{'dentro' if inside else 'fuera'} de ese rango.", "",
            *area_rows(table, s), "",
            "Hectáreas; píxeles de 20 m; un píxel pertenece a la comuna que contiene su centro. "
            "Tabla completa, con no quemado y sin dato: "
            "[`datos/superficies.csv`](datos/superficies.csv).", "",
            "### Validación", "",
            "Dos referencias, dos caras del error. El protocolo y la regla que elige la cifra "
            "principal se escribieron **antes** de ver cualquier resultado.", "",
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
            "**Regla de la cifra principal, fijada de antemano:** se publica el umbral con mayor "
            "exactitud ponderada y el otro como rango; si difieren en menos de "
            f"{rate(estudio.HEADLINE_TIE_POINTS, s)} punto, se publica el de todas las clases. "
            f"Ganó moderada baja o más, por {rate(margin, s)} puntos. Todas las cifras las "
            "reproduce [`notebooks/02_validacion.ipynb`](notebooks/02_validacion.ipynb) y "
            "quedan en [`datos/metricas.json`](datos/metricas.json).",
        ]
    else:
        lines = [
            "## Results", "",
            f"**{number(head['hectares'], s)} ha burned at moderate-low severity or higher**, "
            f"within a range up to {number(head['range_up_to_hectares'], s)} ha if low severity "
            f"is included. Copernicus EMSR715 delineated {number(reference, s)} ha in the same "
            f"area, {'inside' if inside else 'outside'} that range.", "",
            *area_rows(table, s), "",
            "Hectares; 20 m pixels; a pixel belongs to the municipality that contains its "
            "centre. Full table, including unburned and no data: "
            "[`datos/superficies.csv`](datos/superficies.csv).", "",
            "### Validation", "",
            "Two references, two faces of the error. The protocol and the rule that picks the "
            "headline figure were written down **before** any result was seen.", "",
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
            "**Headline rule, fixed beforehand:** publish the threshold with the higher "
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
    stale = []
    for spanish, name in FILES.items():
        path = args.raiz / name
        text = path.read_text(encoding="utf-8")
        new = updated(text, block(metrics, table, spanish), spanish)
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
