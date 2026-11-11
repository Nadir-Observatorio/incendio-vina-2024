"""The study parameters are declared once; these tests catch any copy that drifts from them."""
import json
import re
from pathlib import Path

import pytest

from severidad import estudio, web
from severidad.calculo import CLASSES
from severidad.errors import DataError, SourceError

ROOT = Path(__file__).resolve().parent.parent


class TestParameters:
    def test_thresholds_are_burned_classes(self):
        """Thresholds must point at burned classes (low severity or more)."""
        burned = {code for code, name, _lo, _hi in CLASSES if "severity" in name}
        assert set(estudio.THRESHOLDS.values()) <= burned

    def test_palette_covers_every_class(self):
        """Every class code, and no data, must have a colour."""
        assert set(estudio.PALETTE) == {0} | {code for code, *_ in CLASSES}

    def test_parameters_are_read_only(self):
        """A notebook must not be able to change the study by assignment."""
        with pytest.raises(TypeError):
            estudio.THRESHOLDS["A"] = 3  # type: ignore[index]

    def test_hex_colour_unburned_visible_on_paper(self):
        """Unburned is transparent on the web but must be a visible grey on paper."""
        assert estudio.hex_colour(3) == "#efefef" and estudio.hex_colour(7) == "#a50026"


class TestPublishedFilesAgree:
    def test_metrics_protocol_matches(self):
        """datos/metricas.json must record the same seed and municipalities as the study."""
        metrics = json.loads((ROOT / "datos" / "metricas.json").read_text(encoding="utf-8"))
        protocol = metrics["protocol"]
        assert protocol["seed"] == estudio.SEED
        assert protocol["municipalities"] == list(estudio.MUNICIPALITIES)

    def test_web_map_legend_matches_palette(self):
        """docs/index.html must show the class names and colours of the study."""
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        block = re.search(r"const CLASSES = \[(.*?)\];", html, re.S)
        assert block is not None
        rows = re.findall(r'\["([^"]+)", (null|"rgb\((\d+),(\d+),(\d+)\)")\]', block.group(1))
        by_name = {name: code for code, name in estudio.CLASS_NAMES.items()}
        assert len(rows) == len(by_name)
        for label, colour, r, g, b in rows:
            name = label.replace(" (transparent)", "")
            code = by_name[name]
            if colour == "null":
                assert estudio.PALETTE[code][3] == 0
            else:
                assert (int(r), int(g), int(b)) == estudio.PALETTE[code][:3]

    def test_web_module_uses_study_palette(self):
        """The PNG of the web map must be coloured with the study palette, not a copy."""
        assert web.PALETTE is estudio.PALETTE

    def test_citation_points_to_the_repository(self):
        """CITATION.cff cites the published repository and carries no version label."""
        cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        assert re.search(r"^version:", cff, re.M) is None
        assert re.search(r'^repository-code: "https://github\.com/[^"]+"', cff, re.M)


class TestErrors:
    def test_errors_keep_builtin_bases(self):
        """Existing `except ValueError` and `except OSError` clauses must keep catching them."""
        assert issubclass(DataError, ValueError) and issubclass(SourceError, OSError)
