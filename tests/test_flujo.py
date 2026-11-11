"""The study pipeline (estudio.run and its parts) and the `python -m severidad` command."""
import csv
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import box

from severidad import __main__ as cli
from severidad import estudio
from severidad.errors import DataError

ROOT = Path(__file__).resolve().parent.parent
T = from_origin(260000, 6350000, 20, 20)
CRS = "EPSG:32719"


def _m2(a: float, b: float) -> dict:
    return {"A": {"weighted_accuracy_pct": a, "predicted_burned_ha": 900.0},
            "B": {"weighted_accuracy_pct": b, "predicted_burned_ha": 600.0}}


class TestHeadline:
    def test_higher_accuracy_wins(self):
        """B must be the headline when its weighted accuracy is higher by 1 point or more."""
        h = estudio.headline(_m2(95.2, 96.8))
        assert h == {"threshold": "B", "hectares": 600.0, "range_up_to_hectares": 900.0}

    def test_tie_publishes_a(self):
        """A difference under 1 point must publish A, as the rule fixed before measuring says."""
        assert estudio.headline(_m2(95.2, 96.1))["threshold"] == "A"

    def test_a_wins_when_higher(self):
        """A must win when it is the more accurate threshold."""
        assert estudio.headline(_m2(97.5, 96.0))["threshold"] == "A"


class TestWriters:
    def test_areas_format(self, tmp_path):
        """superficies.csv: one row per class, names quoted only when needed, 2 decimals."""
        table = {"Quilpu\u00e9": {code: code * 1.005 for code in range(8)}}
        path = estudio.write_areas(table, tmp_path / "s.csv")
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "municipality,class_code,class_name,hectares"
        assert lines[1] == "Quilpu\u00e9,0,No data,0.00"
        assert lines[2] == 'Quilpu\u00e9,1,"Enhanced regrowth, high",1.00'
        assert len(lines) == 9 and b"\r" not in path.read_bytes()

    def test_writers_refuse_to_overwrite(self, tmp_path):
        """A published file must never be replaced silently."""
        (tmp_path / "x").write_text("old")
        with pytest.raises(FileExistsError):
            estudio.write_json({}, tmp_path / "x")
        with pytest.raises(FileExistsError):
            estudio.write_areas({}, tmp_path / "x")
        assert (tmp_path / "x").read_text() == "old"

    def test_json_bytes_same_on_every_system(self, tmp_path):
        """JSON must be UTF-8 with LF endings even on Windows, where text mode writes CRLF and the
        published huellas.json would no longer match byte for byte."""
        p = estudio.write_json({"a": "Vi\u00f1a"}, tmp_path / "a.json")
        assert p.read_bytes() == '{\n  "a": "Vi\u00f1a"\n}\n'.encode()

    def test_metrics_protocol_from_parameters(self):
        """metricas.json must describe the protocol with the study parameters, not copies."""
        m = estudio.metrics("abc", {"x": 1}, _m2(95.2, 96.8))
        assert m["protocol"]["seed"] == estudio.SEED
        assert m["protocol"]["municipalities"] == list(estudio.MUNICIPALITIES)
        assert m["references"]["EMSR715"]["sha256"] == estudio.REFERENCES["EMSR715"]["sha256"]
        assert m["headline"]["threshold"] == "B"


class TestMeasurements:
    def test_m1_excludes_outside_and_no_data(self, monkeypatch):
        """Damage points outside the area or on no data must not enter the universe."""
        classes = np.full((10, 10), 5, dtype=np.uint8)
        classes[0, 0] = 0
        in_area = np.ones_like(classes, dtype=bool)
        in_area[:, 8:] = False
        xy = np.array([[260010, 6349990],      # pixel (0, 0): no data
                       [260170, 6349990],      # column 8: outside the area
                       [260050, 6349950],      # inside, class 5
                       [260090, 6349850]])     # inside, class 5
        grades = np.array(["3", "3", "3", "1"])
        monkeypatch.setattr(estudio.validacion, "emsn188_damage", lambda _p, _c: (xy, grades))
        monkeypatch.setattr(estudio, "M1_MIN_DISTANCE_M", 0.0)
        m1 = estudio.measurement_1(classes, T, CRS, in_area, Path("unused"))
        assert (m1["in_municipalities"], m1["in_no_data"], m1["universe"]) == (3, 1, 2)
        assert m1["B"]["in_burned_class"] == 2 and m1["B"]["destroyed_n"] == 1

    def test_m2_domain_is_area_and_footprint(self, monkeypatch):
        """Pixels outside the image footprint must not count in the domain."""
        classes = np.full((20, 20), 3, dtype=np.uint8)
        classes[:, :10] = 6
        in_area = np.ones_like(classes, dtype=bool)
        burned = [box(260000, 6349800, 260200, 6350000)]          # left half, rows 0 to 9
        footprint = [box(260000, 6349700, 260400, 6350000)]       # rows 0 to 14 only
        monkeypatch.setattr(estudio.validacion, "emsr715_delineation",
                            lambda _p, _c: (burned, footprint))
        monkeypatch.setattr(estudio, "SAMPLE_SIZE", 5)
        monkeypatch.setattr(estudio, "M2_MIN_DISTANCE_PX", 0)
        m2 = estudio.measurement_2(classes, T, CRS, in_area, Path("unused"))
        assert m2["domain_pixels"] == 300 and m2["reference_burned_ha"] == 4.0
        assert m2["B"]["predicted_burned_ha"] == 6.0


class TestInputs:
    def test_wrong_municipalities_rejected(self, tmp_path):
        """A boundaries file with other municipalities must not be used silently."""
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"CUT_COM": "13101"}, "geometry": None}]}
        (tmp_path / estudio.MUNICIPALITIES_FILE).write_text(json.dumps(fc), encoding="utf-8")
        with pytest.raises(DataError, match="expected"):
            estudio.load_municipalities(tmp_path, download=False)

    def test_missing_boundaries_without_download(self, tmp_path):
        """With downloads disabled, a missing boundaries file must be an error, not a request."""
        with pytest.raises(DataError, match="download disabled"):
            estudio.load_municipalities(tmp_path, download=False)

    def test_missing_reference_is_reported(self, tmp_path):
        """A missing Copernicus reference must stop the run before any computation."""
        with pytest.raises(FileNotFoundError):
            estudio.check_references(tmp_path)

    def test_run_refuses_existing_folder(self, tmp_path):
        """run must never write into an existing folder."""
        with pytest.raises(FileExistsError):
            estudio.run(tmp_path, tmp_path)


class TestVerification:
    def test_published_documents_match(self):
        """The README files and the web map must carry the published figures."""
        assert estudio.compare_documents(ROOT) == []

    def test_changed_figure_is_detected(self, tmp_path):
        """A figure edited by hand in a README must be reported."""
        root = tmp_path / "repo"
        for name in ("README.en.md", "README.md", "docs", "datos"):
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(source, root / name)
            else:
                root.mkdir(exist_ok=True)
                shutil.copy(source, root / name)
        readme = root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("96,8 %", "97,8 %"),
                          encoding="utf-8")
        assert any("96,8 %" in p for p in estudio.compare_documents(root))

    def test_changed_table_cell_is_detected(self, tmp_path):
        """A hectare figure edited by hand in a README table must be reported."""
        root = tmp_path / "repo"
        shutil.copytree(ROOT / "docs", root / "docs")
        shutil.copytree(ROOT / "datos", root / "datos")
        shutil.copy(ROOT / "README.md", root / "README.md")
        text = (ROOT / "README.en.md").read_text(encoding="utf-8")
        changed = text.replace("| 1,154.4 |", "| 1,154.5 |")
        (root / "README.en.md").write_text(changed, encoding="utf-8")
        assert any("row of Valpara" in p for p in estudio.compare_documents(root))

    def test_changed_total_is_detected(self, tmp_path):
        """The bold total row of a README table must also match the published areas."""
        root = tmp_path / "repo"
        shutil.copytree(ROOT / "docs", root / "docs")
        shutil.copytree(ROOT / "datos", root / "datos")
        shutil.copy(ROOT / "README.en.md", root / "README.en.md")
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        (root / "README.md").write_text(text.replace("**6.759,7**", "**6.760,0**"),
                                           encoding="utf-8")
        assert "README.md: total row does not match superficies.csv" in \
            estudio.compare_documents(root)

    def test_changed_metric_is_detected(self, tmp_path):
        """A metric changed in a run folder must be reported even if the format is the same."""
        out = tmp_path / "out"
        shutil.copytree(ROOT / "datos", out)
        m = json.loads((out / "metricas.json").read_text(encoding="utf-8"))
        m["measurement_2"]["B"]["commission_pct"] = 11.8
        (out / "metricas.json").write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
        assert estudio.compare_outputs(out, ROOT / "datos") == ["metricas.json: content differs"]

    def test_identical_outputs(self):
        """datos/ compared with itself must show no difference."""
        assert estudio.compare_outputs(ROOT / "datos", ROOT / "datos") == []

    def test_changed_area_is_detected(self, tmp_path):
        """One changed hectare figure in a run folder must be reported."""
        out = tmp_path / "out"
        shutil.copytree(ROOT / "datos", out)
        rows = list(csv.reader((out / "superficies.csv").open(encoding="utf-8")))
        rows[1][3] = "280.13"
        with (out / "superficies.csv").open("w", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerows(rows)
        assert estudio.compare_outputs(out, ROOT / "datos") == ["superficies.csv: bytes differ"]


def _published() -> tuple[dict, dict]:
    m = json.loads((ROOT / "datos" / "metricas.json").read_text(encoding="utf-8"))
    return m, estudio.read_areas(ROOT / "datos" / "superficies.csv")


def _viewer() -> dict:
    """A DATA block as the viewer generator (visor/make_viewer.py) embeds it."""
    m, table = _published()
    return {
        "headline": dict(m["headline"]),
        "m1": {"A": {"pct": m["measurement_1"]["A"]["pct"]}},
        "m2": {"reference_burned_ha": m["measurement_2"]["reference_burned_ha"],
               "B": {"weighted_accuracy_pct": m["measurement_2"]["B"]["weighted_accuracy_pct"],
                     "commission_pct": m["measurement_2"]["B"]["commission_pct"],
                     "matrix": m["measurement_2"]["B"]["matrix"]}},
        "by_municipality": [{"name": name, "ha": {str(c): v for c, v in table[name].items()}}
                            for name in estudio.MUNICIPALITIES.values()],
        "texts": {"es": "Superficie quemada", "en": "Burned area"},
        "calibrated": {"chosen": {"hectares": _adjustment()["chosen"]["hectares"]}},
    }


def _adjustment() -> dict:
    return json.loads((ROOT / "datos" / estudio.ADJUSTMENT_FILE).read_text(encoding="utf-8"))


def _page(block: str) -> str:
    return f"<script>\n  const CLASSES = [];\n  const DATA = {block};\n  draw(DATA);\n</script>"


class TestViewer:
    def test_matching_block(self):
        """A DATA block taken from the published files must show no difference."""
        m, table = _published()
        data = estudio.viewer_data(_page(json.dumps(_viewer())))
        assert data is not None and estudio.compare_viewer(data, m, table) == []

    def test_page_without_block(self, tmp_path):
        """A viewer without DATA block shows figures nobody checks: it must be reported."""
        assert estudio.viewer_data("<script>const CLASSES = [];</script>") is None
        root = self._repo_with_viewer(tmp_path, "<p>6,760 ha</p>")
        assert estudio.compare_documents(root) == [
            "docs/index.html: no `const DATA` block, its figures cannot be checked"]

    def test_changed_figure(self):
        """A figure that differs from metricas.json by 0.1 must be reported with its place."""
        m, table = _published()
        data = _viewer()
        data["m2"]["B"]["commission_pct"] = 11.8
        assert estudio.compare_viewer(data, m, table) == [
            "docs/index.html: DATA.m2.B.commission_pct: 11.8 but metricas.json has 11.7"]

    def test_rounded_figure_is_a_difference(self):
        """The block carries the published value; rounding is the browser's job."""
        m, table = _published()
        data = _viewer()
        data["headline"]["hectares"] = 6760
        assert len(estudio.compare_viewer(data, m, table)) == 1

    def test_figure_without_source(self):
        """A key that metricas.json does not have must be refused: no figure without backing."""
        m, table = _published()
        data = _viewer()
        data["m1"]["A"]["pct_rounded"] = 55
        assert estudio.compare_viewer(data, m, table) == [
            "docs/index.html: DATA.m1.A.pct_rounded: not in metricas.json"]

    def test_changed_matrix_cell(self):
        """Lists (the confusion matrix) must be compared element by element."""
        m, table = _published()
        data = _viewer()
        data["m2"]["B"]["matrix"][1][0] = 8
        assert estudio.compare_viewer(data, m, table) == [
            "docs/index.html: DATA.m2.B.matrix[1][0]: 8 but metricas.json has 7"]

    def test_boolean_is_not_a_number(self):
        """true must not pass for 1 (JavaScript would print it as a word)."""
        assert estudio._subset(True, 1, "x") == ["x: True but metricas.json has 1"]

    def test_text_value_must_match(self):
        """The headline threshold is text and must also match."""
        m, table = _published()
        data = _viewer()
        data["headline"]["threshold"] = "A"
        assert len(estudio.compare_viewer(data, m, table)) == 1

    @pytest.mark.parametrize("key", ["headline", "m1", "m2"])
    def test_missing_or_empty_section(self, key):
        """An empty or missing section would check nothing and must be reported."""
        m, table = _published()
        data = _viewer()
        data[key] = {}
        assert estudio.compare_viewer(data, m, table) == [
            f"docs/index.html: DATA.{key} missing or empty"]
        del data[key]
        assert estudio.compare_viewer(data, m, table) == [
            f"docs/index.html: DATA.{key} missing or empty"]

    def test_changed_hectares(self):
        """One hectare figure of one municipality must be reported."""
        m, table = _published()
        data = _viewer()
        data["by_municipality"][1]["ha"]["5"] += 0.01
        problems = estudio.compare_viewer(data, m, table)
        assert len(problems) == 1 and "by_municipality[1].ha[5]" in problems[0]

    def test_municipalities_in_study_order(self):
        """Swapped municipalities must be reported, not silently matched by name."""
        m, table = _published()
        data = _viewer()
        rows = data["by_municipality"]
        rows[0], rows[1] = rows[1], rows[0]
        assert len(estudio.compare_viewer(data, m, table)) == 2

    def test_missing_municipality(self):
        """Three municipalities instead of four must be reported."""
        m, table = _published()
        data = _viewer()
        data["by_municipality"].pop()
        assert "must list 4 municipalities" in estudio.compare_viewer(data, m, table)[0]

    def test_missing_class(self):
        """Every class of superficies.csv must be in the block, and no other."""
        m, table = _published()
        data = _viewer()
        del data["by_municipality"][2]["ha"]["0"]
        data["by_municipality"][3]["ha"]["9"] = 0.0
        problems = estudio.compare_viewer(data, m, table)
        assert len(problems) == 2 and all(".ha: classes" in p for p in problems)

    def test_missing_hectares_object(self):
        """A municipality without its `ha` object must be reported."""
        m, table = _published()
        data = _viewer()
        del data["by_municipality"][0]["ha"]
        assert estudio.compare_viewer(data, m, table) == [
            "docs/index.html: DATA.by_municipality[0].ha: missing"]

    @pytest.mark.parametrize(("block", "message"), [
        ('{"headline": {"hectares": 6759.7,}}', "not strict JSON"),
        ('{"headline": {"hectares": NaN}}', "not strict JSON"),
        ("[1, 2]", "not an object"),
    ])
    def test_malformed_block(self, block, message):
        """A block that is not strict JSON must stop the check instead of passing."""
        with pytest.raises(DataError, match=message):
            estudio.viewer_data(_page(block))

    def test_block_must_end_with_semicolon(self):
        """Text after the object other than `;` means the block was not read whole."""
        with pytest.raises(DataError, match="must end with"):
            estudio.viewer_data('const DATA = {"a": 1} + 1;')

    def test_repeated_block(self):
        """Two DATA blocks would let the page show one and the check read the other."""
        with pytest.raises(DataError, match="appears 2 times"):
            estudio.viewer_data(_page("{}") + _page("{}"))

    def _repo_with_viewer(self, tmp_path, html: str) -> Path:
        root = tmp_path / "repo"
        shutil.copytree(ROOT / "datos", root / "datos")
        (root / "docs").mkdir()
        (root / "docs" / "index.html").write_text(html, encoding="utf-8")
        for name in ("README.en.md", "README.md"):
            shutil.copy(ROOT / name, root / name)
        return root

    def test_documents_use_the_block_when_present(self, tmp_path):
        """compare_documents must read the block and report a figure that differs."""
        root = self._repo_with_viewer(tmp_path, _page(json.dumps(_viewer())))
        assert estudio.compare_documents(root) == []
        data = _viewer()
        data["m1"]["A"]["pct"] = 54.6
        root2 = self._repo_with_viewer(tmp_path / "b", _page(json.dumps(data)))
        assert estudio.compare_documents(root2) == [
            "docs/index.html: DATA.m1.A.pct: 54.6 but metricas.json has 54.7"]

    def test_verify_stops_on_malformed_block(self, tmp_path):
        """`verify` must exit 2 (could not check) when the block cannot be read."""
        root = self._repo_with_viewer(tmp_path, _page("{oops}"))
        assert cli.main(["verify", "--root", str(root)]) == 2


class TestDownloadCommand:
    def test_download_asks_both_scenes(self, monkeypatch, tmp_path):
        """`download` must ask the two study scenes for the colour composite, nothing else."""
        asked = []

        def fake(scene_id, folder, timeout=600.0, bands=("x",)):
            asked.append((scene_id, tuple(bands)))
            return {b: Path(folder) / scene_id / f"{b}.tif" for b in bands}

        monkeypatch.setattr(estudio.fuente, "download_scene", fake)
        assert cli.main(["download", "--raw", str(tmp_path)]) == 0
        assert asked == [(estudio.PRE_SCENE, ("visual",)), (estudio.POST_SCENE, ("visual",))]

    def test_download_failure_exits_2(self, monkeypatch, tmp_path):
        """A network failure must end with exit code 2 and a message, not a traceback."""
        from severidad.errors import SourceError

        def fail(*args, **kwargs):
            raise SourceError("refused")

        monkeypatch.setattr(estudio.fuente, "download_scene", fail)
        assert cli.main(["download", "--raw", str(tmp_path), "--bands", "red"]) == 2


class TestSmallParts:
    def test_boundaries_downloaded_once(self, tmp_path, monkeypatch):
        """Missing boundaries are downloaded once and then read from the raw folder."""
        calls = []
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"CUT_COM": c}, "geometry": None}
            for c in estudio.MUNICIPALITIES]}

        def fake(codes, timeout=120.0):
            calls.append(dict(codes))
            return fc

        monkeypatch.setattr(estudio.fuente, "municipalities", fake)
        assert estudio.load_municipalities(tmp_path) == fc
        assert estudio.load_municipalities(tmp_path) == fc
        assert calls == [dict(estudio.MUNICIPALITIES)]

    def test_repackaged_emsr715_accepted_by_its_product(self, tmp_path, monkeypatch):
        """A re-zipped EMSR715 download is accepted only if the delineation inside is the one."""
        product = estudio.REFERENCES["EMSR715"]["product_sha256"]

        def check(path, size, digest):
            if "EMSR715" in path.name:
                raise DataError("outer differs")

        monkeypatch.setattr(estudio.validacion, "check_file", check)
        monkeypatch.setattr(estudio.validacion, "inner_sha256", lambda outer, name: product)
        refs = estudio.check_references(tmp_path)
        assert refs["EMSR715"].name == "EMSR715_products.zip"
        monkeypatch.setattr(estudio.validacion, "inner_sha256", lambda outer, name: "other")
        with pytest.raises(DataError, match="is not the one used"):
            estudio.check_references(tmp_path)

    def test_run_folder_missing_file(self, tmp_path):
        """An incomplete run folder must name the missing files instead of comparing."""
        (tmp_path / "clases.tif").write_bytes(b"x")
        problems = estudio.compare_outputs(tmp_path, ROOT / "datos")
        assert problems == [f"{n}: missing in {tmp_path}" for n in
                            ("superficies.csv", "huellas.json", "metricas.json")]

    def test_shape_mismatch_in_block(self):
        """An object where metricas.json has a number, or a list of another length, differ."""
        assert estudio._subset({"a": 1}, 5, "x") == ["x: is an object, metricas.json has int"]
        assert estudio._subset([1, 2], [1, 2, 3], "x") == ["x: list differs from metricas.json"]

    def test_read_classes(self):
        """The published raster must read back in the study CRS."""
        classes, transform, crs = estudio.read_classes(ROOT / "datos" / "clases.tif")
        assert crs == CRS and classes.ndim == 2 and transform.a == 20


class TestCommand:
    def test_verify_ok(self):
        """`verify` on the repository must exit 0."""
        assert cli.main(["verify", "--root", str(ROOT)]) == 0

    def test_verify_reports_differences(self, tmp_path):
        """`verify` must exit 1 when a run folder differs from datos/."""
        out = tmp_path / "out"
        shutil.copytree(ROOT / "datos", out)
        (out / "huellas.json").write_text("{}\n", encoding="utf-8")
        assert cli.main(["verify", "--root", str(ROOT), "--out", str(out)]) == 1

    def test_run_into_existing_folder_fails_cleanly(self, tmp_path):
        """`run` into an existing folder must exit 2 with a message, not a traceback."""
        assert cli.main(["run", "--out", str(tmp_path), "--raw", str(tmp_path)]) == 2
