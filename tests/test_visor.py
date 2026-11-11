"""The web viewer is built from datos/ only and keeps the two contracts with the published data
(`severidad`): `const CLASSES` (tests/test_estudio.py) and `const DATA` (the `const DATA` contract
checked by `python -m severidad verify`)."""
import importlib.util
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from severidad import estudio

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "visor" / "make_viewer.py"
spec = importlib.util.spec_from_file_location("make_viewer", SCRIPT)
visor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(visor)

SCENES = [ROOT / "datos_crudos" / s / "visual.tif" for s in (estudio.PRE_SCENE, estudio.POST_SCENE)]
needs_scenes = pytest.mark.skipif(not all(p.exists() for p in SCENES),
                                  reason="run 'python -m severidad download --bands visual' first")
needs_built = pytest.mark.skipif(not (ROOT / "datos" / "contexto" / "construido.tif").exists(),
                                 reason="run 'python -m severidad.contexto derivar' first")


def _metrics():
    return json.loads((ROOT / "datos" / "metricas.json").read_text(encoding="utf-8"))


def _table():
    return estudio.read_areas(ROOT / "datos" / "superficies.csv")


@pytest.fixture(scope="module")
def page():
    _grid, corners = visor.mercator_grid()
    data = visor.viewer_data(corners)
    return data, visor.render(data)


class TestContracts:
    def test_data_block_passes_verify(self, page):
        """What verify reads back from the page must match datos/ exactly."""
        _data, html = page
        shown = estudio.viewer_data(html)
        assert shown is not None
        assert estudio.compare_viewer(shown, _metrics(), _table()) == []

    def test_a_changed_figure_is_caught(self, page):
        data, _html = page
        wrong = json.loads(json.dumps(data))
        wrong["headline"]["hectares"] = 6760.0
        wrong["by_municipality"][0]["ha"]["7"] += 0.1
        assert len(estudio.compare_viewer(wrong, _metrics(), _table())) == 2

    def test_legend_block_is_the_one_test_estudio_reads(self, page):
        _data, html = page
        block = re.search(r"const CLASSES = \[(.*?)\];", html, re.S)
        rows = re.findall(r'\["([^"]+)", (null|"rgb\((\d+),(\d+),(\d+)\)")\]', block.group(1))
        by_name = {name: code for code, name in estudio.CLASS_NAMES.items()}
        assert len(rows) == len(by_name)
        for label, colour, r, g, b in rows:
            code = by_name[label.replace(" (transparent)", "")]
            if colour == "null":
                assert estudio.PALETTE[code][3] == 0
            else:
                assert (int(r), int(g), int(b)) == estudio.PALETTE[code][:3]

    def test_no_figure_is_written_by_hand(self, page):
        """Outside the DATA block the page holds no published figure, in either format."""
        _data, html = page
        start = html.index("const DATA = ")
        rest = html[:start] + html[html.index(";\n", start):]
        m = _metrics()
        hectares = [m["headline"]["hectares"], m["headline"]["range_up_to_hectares"],
                    m["measurement_2"]["reference_burned_ha"]]
        rates = [m["measurement_2"]["B"]["weighted_accuracy_pct"],
                 m["measurement_2"]["B"]["commission_pct"], m["measurement_1"]["A"]["pct"]]
        spanish = str.maketrans({",": ".", ".": ","})
        for v in hectares:            # the rounded form too: the old page wrote "6,760 ha"
            for text in (f"{v}", f"{v:,.1f}", f"{v:,.1f}".translate(spanish), f"{round(v):,}",
                         f"{round(v):,}".translate(spanish)):
                assert text not in rest, text
        for v in rates:
            for text in (f"{v} %", f"{v}".replace(".", ",") + " %"):
                assert text not in rest, text

    def test_markers_are_all_replaced(self, page):
        _data, html = page
        assert "/*CLASSES*/" not in html and "/*DATA*/" not in html and "@SRI_" not in html
        assert html.count("const DATA = ") == 1

    def test_maplibre_is_local_with_integrity(self, page):
        _data, html = page
        assert "unpkg.com" not in html
        for name in ("maplibre-gl.js", "maplibre-gl.css"):
            sri = visor.sri(visor.VENDOR / name)
            assert f'vendor/maplibre-gl/{name}" integrity="{sri}"' in html


class TestVendor:
    def test_pinned_files_pass(self):
        visor.check_vendor()

    def test_a_changed_file_is_refused(self, tmp_path):
        folder = tmp_path / "maplibre-gl"
        shutil.copytree(visor.VENDOR, folder)
        (folder / "maplibre-gl.js").write_bytes(b"// not maplibre")
        with pytest.raises(ValueError, match="pinned"):
            visor.check_vendor(folder)


class TestLayers:
    @needs_built
    def test_built_outline_is_inside_the_mask_and_on_the_grid(self):
        import rasterio

        from severidad import web

        grid, _corners = visor.mercator_grid()
        outline = visor.built_outline(grid)
        assert outline.shape == (grid["height"], grid["width"], 4)
        with rasterio.open(ROOT / "datos" / "contexto" / "construido.tif") as r:
            warped, _t = web.to_web_mercator(r.read(1), r.transform, r.crs.to_string())
        drawn = outline[..., 3] > 0
        assert drawn.any() and not (drawn & (warped != 1)).any()
        assert drawn.sum() < (warped == 1).sum()        # an outline, not a fill

    def test_missing_scene_says_how_to_get_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(visor, "RAW", tmp_path)
        grid, _corners = visor.mercator_grid()
        with pytest.raises(FileNotFoundError, match="download --bands visual"):
            visor.true_colour(estudio.PRE_SCENE, grid)

    @needs_scenes
    def test_true_colour_fills_the_window(self):
        grid, _corners = visor.mercator_grid()
        rgb = visor.true_colour(estudio.POST_SCENE, grid)
        assert rgb.shape == (grid["height"], grid["width"], 3) and rgb.dtype == np.uint8
        assert rgb.any(axis=2).all()


@needs_scenes
@needs_built
class TestBuild:
    def _docs(self, tmp_path):
        docs = tmp_path / "docs"
        (docs / "vendor").mkdir(parents=True)
        shutil.copytree(visor.VENDOR, docs / "vendor" / "maplibre-gl")
        shutil.copy(ROOT / "docs" / "clases.png", docs / "clases.png")
        return docs

    def test_writes_page_and_layers_and_refuses_to_overwrite(self, tmp_path):
        docs = self._docs(tmp_path)
        written = visor.build(docs=docs)
        assert {p.relative_to(docs).as_posix() for p in written} == {
            "index.html", "capas/construido.png", "capas/antes.jpg", "capas/despues.jpg"}
        with pytest.raises(FileExistsError):
            visor.build(docs=docs)
        visor.build(force=True, docs=docs)

    def test_same_inputs_same_page(self, tmp_path):
        a, b = self._docs(tmp_path / "a"), self._docs(tmp_path / "b")
        visor.build(docs=a)
        visor.build(docs=b)
        for name in ("index.html", "capas/construido.png", "capas/antes.jpg"):
            assert (a / name).read_bytes() == (b / name).read_bytes(), name
