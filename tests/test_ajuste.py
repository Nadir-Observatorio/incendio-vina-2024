"""Local threshold adjustment (severidad.ajuste) on synthetic data; the real run is checked
against the published file when the raw data are present."""
import hashlib
import json
import logging
import shutil
from pathlib import Path

import numpy as np
import pytest
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import box
from test_calculo import _pair

from severidad import __main__ as cli
from severidad import ajuste, calculo, estudio
from severidad.errors import DataError

ROOT = Path(__file__).resolve().parent.parent
T = from_origin(260000, 6350000, 20, 20)
CRS = "EPSG:32719"
SIDE = 600


class TestIndices:
    def test_formulas(self):
        """RdNBR and RBR follow Miller and Thode (2007) and Parks et al. (2014), dNBR x 1000."""
        dnbr = np.array([300.0, 300.0, -50.0, np.nan])
        pre = np.array([0.25, 0.0001, -0.36, 0.5])
        out = ajuste.relative_indices(dnbr, pre)
        assert out["dNBR"][0] == 300.0
        assert out["RdNBR"][0] == pytest.approx(600.0)
        assert out["RdNBR"][1] == pytest.approx(300.0 / np.sqrt(0.001))
        assert out["RdNBR"][2] == pytest.approx(-50.0 / 0.6)
        assert out["RBR"][0] == pytest.approx(300.0 / 1.251)
        assert out["RBR"][2] == pytest.approx(-50.0 / 0.641)
        assert all(np.isnan(v[3]) for v in out.values())

    def test_missing_pre_nbr_is_nan(self):
        """A pixel without pre-fire NBR has no index at all, not a dNBR-only value."""
        out = ajuste.relative_indices(np.array([200.0]), np.array([np.nan]))
        assert all(np.isnan(v[0]) for v in out.values())

    def test_shape_mismatch(self):
        with pytest.raises(DataError):
            ajuste.relative_indices(np.zeros(3), np.zeros(4))

    def test_same_dnbr_as_the_published_method(self, tmp_path):
        """The dNBR used here is exactly the one of calculo.severity, and the pre-fire NBR is the
        one calculo computes on the same window."""
        pre, post, bbox = _pair(tmp_path)
        r = calculo.severity(pre, post, bbox)
        nbr = ajuste.pre_nbr(pre, bbox)
        w = calculo.window(pre["nir08"], bbox)
        nir, swir, _scl, _ = calculo._reflectance(pre, w)
        assert np.array_equal(nbr, calculo._nbr(nir, swir), equal_nan=True)
        out = ajuste.relative_indices(r.dnbr, nbr)
        assert np.array_equal(out["dNBR"], r.dnbr, equal_nan=True)


class TestBlocks:
    def test_block_ids(self):
        ids = ajuste.block_ids((250, 120), 100)
        assert ids[0, 0] == 0 and ids[0, 99] == 0 and ids[0, 100] == 1
        assert ids[100, 0] == 2 and ids[249, 119] == 5

    def test_assignment_splits_every_pair(self):
        """Blocks are split in pairs of similar burned fraction: one per side, nothing lost."""
        blocks = ajuste.block_ids((400, 400), 100)
        domain = np.ones((400, 400), dtype=bool)
        domain[:100, :100] = False                       # block 0 has no domain pixels
        reference = np.zeros((400, 400), dtype=bool)
        reference[:, :150] = True
        fit, val = ajuste.assign_blocks(blocks, domain, reference, np.random.default_rng(1))
        assert set(fit).isdisjoint(val) and set(fit) | set(val) == set(range(1, 16))
        assert abs(len(fit) - len(val)) == 1
        burned = {b: reference[blocks == b].mean() for b in range(1, 16)}
        assert abs(sum(burned[b] for b in fit) - sum(burned[b] for b in val)) <= 1.0

    def test_pairs_follow_burned_fraction(self):
        """Consecutive blocks in burned-fraction order always fall on different sides."""
        blocks = ajuste.block_ids((100, 800), 100)
        domain = np.ones((100, 800), dtype=bool)
        reference = np.zeros((100, 800), dtype=bool)
        shares = [7, 1, 5, 3, 0, 6, 2, 4]                 # tenths of rows burned per block
        for b, share in enumerate(shares):
            reference[: share * 10, b * 100:(b + 1) * 100] = True
        for seed in range(5):
            fit, _val = ajuste.assign_blocks(blocks, domain, reference,
                                             np.random.default_rng(seed))
            order = sorted(range(8), key=lambda b: shares[b])
            for i in range(0, 8, 2):
                assert (order[i] in fit) != (order[i + 1] in fit)

    def test_assignment_is_reproducible(self):
        blocks = ajuste.block_ids((300, 300), 100)
        domain = np.ones((300, 300), dtype=bool)
        reference = np.random.default_rng(0).random((300, 300)) > 0.7
        a = ajuste.assign_blocks(blocks, domain, reference, np.random.default_rng(5))
        b = ajuste.assign_blocks(blocks, domain, reference, np.random.default_rng(5))
        assert all(np.array_equal(x, y) for x, y in zip(a, b, strict=True))

    def test_guard_band_is_chebyshev_below_distance(self):
        area = np.zeros((30, 30), dtype=bool)
        area[:, :10] = True
        near = ajuste.guard_band(area, 10)
        assert near[:, :19].all() and not near[:, 19:].any()
        diagonal = np.zeros((30, 30), dtype=bool)
        diagonal[0, 0] = True
        assert ajuste.guard_band(diagonal, 10)[9, 9] and not ajuste.guard_band(diagonal, 10)[10, 9]


class TestThreshold:
    def _hist(self, burned_values, unburned_values):
        values = np.concatenate([burned_values, unburned_values])
        ref = np.concatenate([np.ones(len(burned_values), bool), np.zeros(len(unburned_values),
                                                                          bool)])
        return ajuste.block_histograms(values, ref, np.zeros(len(values), dtype=np.int64),
                                       np.zeros(1, dtype=np.int64))

    def test_separable_classes(self):
        """With a clean gap the lowest threshold of the gap wins (ties to the lowest)."""
        hb, hu = self._hist(np.array([250.0, 300, 900]), np.array([-40.0, 10, 120.5]))
        k = int(ajuste.best_bin(hb[0], hu[0]))
        assert ajuste.threshold_of(k) == 121

    def test_burned_when_index_reaches_threshold(self):
        """A pixel exactly at the threshold is burned (half-open classes, as calculo)."""
        hb, hu = self._hist(np.array([200.0, 200.0]), np.array([199.99]))
        assert ajuste.threshold_of(int(ajuste.best_bin(hb[0], hu[0]))) == 200

    def test_accuracy_youden_and_balance_differ(self):
        """With few burned pixels, Youden pays omission with commission; accuracy does not."""
        rng = np.random.default_rng(3)
        hb, hu = self._hist(rng.normal(300, 80, 2000), rng.normal(50, 80, 20000))
        accuracy, youden, balance = (ajuste.threshold_of(int(ajuste.best_bin(hb[0], hu[0], c)))
                                     for c in ("accuracy", "youden", "balance"))
        assert youden < accuracy and 150 < youden < 220 and 200 < accuracy < 320
        assert 150 < balance < 320

    def test_unknown_criterion(self):
        hb, hu = self._hist(np.array([1.0]), np.array([0.0]))
        with pytest.raises(DataError):
            ajuste.best_bin(hb[0], hu[0], "auc")

    def test_rows_are_independent(self):
        """Bootstrap rows are solved one by one, as the single-row call would."""
        hb, hu = self._hist(np.array([250.0, 300]), np.array([10.0, 120]))
        hb2, hu2 = self._hist(np.array([500.0]), np.array([300.0, 450]))
        stacked = ajuste.best_bin(np.vstack([hb, hb2]), np.vstack([hu, hu2]))
        assert list(stacked) == [int(ajuste.best_bin(hb[0], hu[0])),
                                 int(ajuste.best_bin(hb2[0], hu2[0]))]

    def test_is_burned(self):
        assert ajuste.is_burned(np.array([199.9, 200.0, np.nan]), 200).tolist() == [False, True,
                                                                                      False]

    def test_values_outside_the_grid_go_to_the_end_bins(self):
        b = ajuste.bins(np.array([-1e9, -2000.0, 0.4, 3999.9, 1e9]))
        assert list(b) == [0, 0, 2000, 5999, 6000]

    def test_bootstrap_weights(self):
        w = ajuste.bootstrap_weights(7, 20, np.random.default_rng(0))
        assert w.shape == (20, 7) and (w.sum(axis=1) == 7).all()

    def test_interval_uses_observed_values(self):
        assert ajuste.interval(np.arange(1000.0)) == [24.0, 974.0]


class TestValidation:
    def test_confusion_and_rates(self):
        predicted = np.array([True, True, False, False, True])
        reference = np.array([True, False, True, False, True])
        blocks = np.array([3, 3, 3, 3, 7])
        counts = ajuste.block_confusion(predicted, reference, blocks, np.array([3, 7]))
        assert counts.tolist() == [[1, 1, 1, 1], [1, 0, 0, 0]]
        r = ajuste.rates(counts.sum(axis=0))
        assert r["accuracy_pct"] == 60.0 and r["commission_pct"] == pytest.approx(100 / 3)
        assert r["omission_pct"] == pytest.approx(100 / 3) and r["area_difference_pct"] == 0.0

    def test_unlisted_blocks_are_ignored(self):
        counts = ajuste.block_confusion(np.array([True]), np.array([True]), np.array([9]),
                                        np.array([3]))
        assert counts.tolist() == [[0, 0, 0, 0]]

    @pytest.mark.parametrize(("accuracy", "expected"), [
        ({"dNBR": 95.0, "RdNBR": 95.9, "RBR": 94.0}, "dNBR"),
        ({"dNBR": 95.0, "RdNBR": 96.0, "RBR": 94.0}, "RdNBR"),
        ({"dNBR": 95.0, "RdNBR": 96.5, "RBR": 96.5}, "RdNBR"),
        ({"dNBR": 97.0, "RdNBR": 96.0, "RBR": 99.0}, "RBR"),
    ])
    def test_choice_rule(self, accuracy, expected):
        """An index replaces dNBR only if it is at least 1 point more accurate in validation."""
        assert ajuste.choose_index(accuracy, estudio.HEADLINE_TIE_POINTS) == expected


# --- End to end on a synthetic fire ---------------------------------------------------------------

def _synthetic(monkeypatch) -> tuple[ajuste.Inputs, dict]:
    monkeypatch.setattr(estudio, "SAMPLE_SIZE", 20)
    monkeypatch.setattr(estudio, "M1_MIN_DISTANCE_M", 0.0)
    monkeypatch.setattr(ajuste, "EMSN188_SAMPLE", 15)
    monkeypatch.setattr(ajuste, "BOOTSTRAP_REPLICATES", 60)
    rng = np.random.default_rng(11)
    burned_poly = box(262000, 6342000, 268000, 6348000)          # rows 100-400, cols 100-400
    reference = rasterize([(burned_poly, 1)], out_shape=(SIDE, SIDE), transform=T, fill=0,
                          dtype="uint8").astype(bool)
    dnbr = np.where(reference, rng.normal(330, 90, (SIDE, SIDE)), rng.normal(30, 70, (SIDE, SIDE)))
    nbr_pre = np.clip(rng.normal(0.35, 0.12, (SIDE, SIDE)), -0.9, 0.9)
    classes = np.zeros((SIDE, SIDE), dtype=np.uint8)
    for code, _name, low, high in calculo.CLASSES:
        classes[(dnbr >= low) & (dnbr < high)] = code
    classes[:5, :] = 0                                           # some no data
    dnbr[:5, :] = np.nan
    in_area = np.ones((SIDE, SIDE), dtype=bool)
    in_area[:, 580:] = False
    footprint_poly = box(260000, 6338200, 272000, 6350000)       # rows 0-590
    footprint = rasterize([(footprint_poly, 1)], out_shape=(SIDE, SIDE), transform=T, fill=0,
                          dtype="uint8").astype(bool)
    xy = np.column_stack([rng.uniform(262100, 271000, 400), rng.uniform(6340000, 6349800, 400)])
    grades = rng.choice(np.array(["1", "2", "3"]), 400)
    monkeypatch.setattr(estudio.validacion, "emsr715_delineation",
                        lambda _p, _c: ([burned_poly], [footprint_poly]))
    monkeypatch.setattr(estudio.validacion, "emsn188_damage", lambda _p, _c: (xy, grades))
    m1 = estudio.measurement_1(classes, T, CRS, in_area, Path("unused"))
    m2 = estudio.measurement_2(classes, T, CRS, in_area, Path("unused"))
    metrics = {"measurement_1": m1, "measurement_2": m2, "headline": estudio.headline(m2)}
    inputs = ajuste.Inputs(classes, ajuste.relative_indices(dnbr, nbr_pre), T, CRS, in_area,
                           reference, footprint, xy, grades)
    return inputs, metrics


class TestAdjust:
    def test_synthetic_fire(self, monkeypatch):
        """The fitted dNBR threshold sits between the two populations, beats A and B on the
        validation blocks, and the published headline travels unchanged."""
        inputs, metrics = _synthetic(monkeypatch)
        out = ajuste.adjust(inputs, metrics)
        d = out["indices"]["dNBR"]
        assert 130 < d["threshold"] < 230
        assert d["threshold_interval"][0] <= d["threshold"] <= d["threshold_interval"][1]
        assert d["hectares_interval"][0] <= d["hectares"] <= d["hectares_interval"][1]
        published = out["published_thresholds_on_validation"]
        assert d["validation"]["accuracy_pct"] >= published["A"]["accuracy_pct"]
        assert d["validation"]["accuracy_pct"] >= published["B"]["accuracy_pct"]
        assert out["chosen"]["headline_unchanged"] == metrics["headline"]
        assert out["domain"]["m2_pixels_excluded"] <= 4 * estudio.SAMPLE_SIZE
        assert out["domain"]["fit_blocks"] > 0 and out["domain"]["validation_blocks"] > 0
        assert out["emsn188"]["sample"] == ajuste.EMSN188_SAMPLE
        assert out["chosen"]["index"] in ajuste.INDICES
        lines = ajuste.summary(out)
        assert len(lines) == 2 + len(ajuste.INDICES) + len(estudio.THRESHOLDS)

    def test_emsn188_takes_every_point_that_fits(self, monkeypatch):
        """Amendment of 2026-11-11: fewer points than the target do not stop the check, and the
        spacing and the validation region stay as declared."""
        inputs, metrics = _synthetic(monkeypatch)
        monkeypatch.setattr(ajuste, "EMSN188_SAMPLE", 10_000)
        e = ajuste.adjust(inputs, metrics)["emsn188"]
        assert 0 < e["sample"] <= e["candidates"] < 10_000 and e["sample_target"] == 10_000
        monkeypatch.setattr(ajuste, "EMSN188_SAMPLE", 15)
        assert ajuste.adjust(inputs, metrics)["emsn188"]["sample"] == 15

    def test_emsn188_sample_keeps_spacing(self, monkeypatch):
        """However few points fit, they keep the measurement 1 spacing."""
        monkeypatch.setattr(ajuste, "EMSN188_SAMPLE", 300)
        xy = np.random.default_rng(2).uniform(0, 2000, (500, 2))
        chosen = ajuste.emsn188_sample(xy, np.arange(500))
        d = np.hypot(*(xy[chosen][:, None, :] - xy[chosen][None, :, :]).transpose(2, 0, 1))
        np.fill_diagonal(d, np.inf)
        assert 0 < len(chosen) < 300 and d.min() >= estudio.M1_MIN_DISTANCE_M

    def test_emsn188_without_points_is_refused(self, monkeypatch):
        inputs, metrics = _synthetic(monkeypatch)
        monkeypatch.setattr(ajuste, "emsn188_candidates", lambda *_a: np.zeros(0, dtype=int))
        with pytest.raises(DataError, match="EMSN188"):
            ajuste.adjust(inputs, metrics)

    def test_reproducible(self, monkeypatch):
        inputs, metrics = _synthetic(monkeypatch)
        assert ajuste.adjust(inputs, metrics) == ajuste.adjust(inputs, metrics)

    def test_fit_and_validation_never_meet(self):
        """No validation pixel is closer than 10 px (Chebyshev) to a fit block, and excluded
        pixels are in neither set."""
        blocks = ajuste.block_ids((300, 300), 100)
        domain = np.ones((300, 300), dtype=bool)
        excluded = np.zeros((300, 300), dtype=bool)
        excluded[150, 150] = excluded[20, 20] = True
        fit, val = np.array([0, 4, 8]), np.array([1, 2, 3, 5, 6, 7])
        fit_px, val_px, _near = ajuste.split_pixels(domain, blocks, fit, val, excluded)
        rows, cols = np.nonzero(np.isin(blocks, fit))
        for r, c in zip(*np.nonzero(val_px), strict=True):
            assert np.maximum(np.abs(rows - r), np.abs(cols - c)).min() >= 10
        assert not (fit_px & val_px).any() and not ((fit_px | val_px) & excluded).any()
        assert val_px[150, 90] and not val_px[150, 91] and fit_px[20, 21]

    def test_m2_pixels_are_the_published_samples(self, monkeypatch):
        """The excluded pixels are exactly the union of the two M2 samples."""
        inputs, metrics = _synthetic(monkeypatch)
        domain = (inputs.classes > 0) & inputs.in_area & inputs.footprint
        mask = ajuste.m2_pixels(inputs.classes, domain, inputs.reference,
                                metrics["measurement_2"])
        union = set()
        for code in estudio.THRESHOLDS.values():
            for part in estudio.validacion.sample_strata(inputs.classes >= code, domain,
                                                         estudio.SAMPLE_SIZE, estudio.SEED,
                                                         estudio.M2_MIN_DISTANCE_PX):
                union |= set(part.tolist())
        assert set(np.flatnonzero(mask).tolist()) == union
        assert ajuste.adjust(inputs, metrics)["domain"]["m2_pixels_excluded"] == len(union)

    def test_emsn188_candidates_skip_m1_and_other_blocks(self):
        universe = np.array([0, 2, 3, 5, 8])
        region_at = np.array([1, 1, 1, 0, 1, 1, 1, 1, 0], dtype=np.uint8)
        taken = np.array([2, 5])
        assert ajuste.emsn188_candidates(universe, region_at, taken).tolist() == [0]

    def test_wrong_published_m2_sample_is_refused(self, monkeypatch):
        """If the M2 sample cannot be rebuilt exactly, the M2 points cannot be excluded."""
        inputs, metrics = _synthetic(monkeypatch)
        metrics["measurement_2"]["A"]["matrix"][0][0] += 1
        with pytest.raises(DataError, match="measurement 2"):
            ajuste.adjust(inputs, metrics)

    def test_wrong_published_m1_sample_is_refused(self, monkeypatch):
        inputs, metrics = _synthetic(monkeypatch)
        metrics["measurement_1"]["B"]["in_burned_class"] += 1
        with pytest.raises(DataError, match="measurement 1"):
            ajuste.adjust(inputs, metrics)

    def test_other_domain_is_refused(self, monkeypatch):
        inputs, metrics = _synthetic(monkeypatch)
        metrics["measurement_2"]["domain_ha"] += 0.1
        with pytest.raises(DataError, match="domain_ha"):
            ajuste.adjust(inputs, metrics)

    def test_index_missing_on_domain_is_refused(self, monkeypatch):
        inputs, metrics = _synthetic(monkeypatch)
        inputs.indices["RBR"][300, 300] = np.nan
        with pytest.raises(DataError, match="RBR"):
            ajuste.adjust(inputs, metrics)


class TestCommand:
    def test_run_refuses_existing_folder(self, tmp_path):
        with pytest.raises(FileExistsError):
            ajuste.run(tmp_path, tmp_path, tmp_path)

    def test_command_writes_and_summarises(self, monkeypatch, tmp_path, caplog):
        def fake(out, raw, published):
            out.mkdir()
            return estudio.write_json(_FAKE, out / ajuste.ADJUSTMENT_FILE)

        monkeypatch.setattr(ajuste, "run", fake)
        caplog.set_level(logging.INFO, logger="severidad")
        code = cli.main(["ajuste", "--out", str(tmp_path / "a"), "--raw", str(tmp_path)])
        assert code == 0 and "chosen dNBR" in caplog.text

    def test_command_existing_folder_exits_2(self, tmp_path):
        assert cli.main(["ajuste", "--out", str(tmp_path), "--raw", str(tmp_path)]) == 2


_V = {"accuracy_pct": 1.0, "accuracy_interval": [0.0, 2.0], "commission_pct": 1.0,
      "omission_pct": 1.0}
_FAKE = {
    "domain": {"domain_ha": 1.0, "reference_burned_ha": 1.0, "fit_blocks": 1,
               "validation_blocks": 1},
    "indices": {n: {"threshold": 1, "threshold_interval": [0, 2], "hectares": 1.0,
                    "hectares_interval": [0.0, 2.0], "validation": _V} for n in ajuste.INDICES},
    "published_thresholds_on_validation": {"A": _V, "B": _V},
    "chosen": {"index": "dNBR", "threshold": 1, "hectares": 1.0, "hectares_interval": [0.0, 2.0]},
}


_RAW = ROOT / "datos_crudos"
_HAVE_RAW = all((_RAW / s / "nir08.tif").exists() for s in (estudio.PRE_SCENE, estudio.POST_SCENE))


@pytest.mark.skipif(not _HAVE_RAW or not (ROOT / "datos" / ajuste.ADJUSTMENT_FILE).exists(),
                    reason="needs the raw scenes and a published adjustment")
def test_published_adjustment_is_reproduced(tmp_path):
    """The adjustment rebuilt from the raw data is the published one."""
    path = ajuste.run(tmp_path / "a", _RAW, ROOT / "datos")
    published = json.loads((ROOT / "datos" / ajuste.ADJUSTMENT_FILE).read_text(encoding="utf-8"))
    assert json.loads(path.read_text(encoding="utf-8")) == published


# --- The calibrated headline in the published documents (verify) ---------------------------------

def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "datos", root / "datos")
    shutil.copytree(ROOT / "docs", root / "docs", ignore=shutil.ignore_patterns("vendor", "capas"))
    for name in ("README.md", "README.en.md"):
        shutil.copy(ROOT / name, root / name)
    return root


class TestVerifyCalibrated:
    def test_published_documents_carry_the_calibrated_headline(self):
        assert estudio.compare_documents(ROOT) == []

    def test_changed_headline_in_readme(self, tmp_path):
        root = _repo(tmp_path)
        adj = json.loads((root / "datos" / ajuste.ADJUSTMENT_FILE).read_text(encoding="utf-8"))
        shown = estudio._number(adj["chosen"]["hectares"], True)
        readme = root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(shown, "9.999,9"),
                          encoding="utf-8")
        assert any(shown in p and "ajuste_umbrales" in p for p in estudio.compare_documents(root))

    def test_changed_rate_in_english_readme(self, tmp_path):
        root = _repo(tmp_path)
        adj = json.loads((root / "datos" / ajuste.ADJUSTMENT_FILE).read_text(encoding="utf-8"))
        v = adj["indices"][adj["chosen"]["index"]]["validation"]["omission_pct"]
        readme = root / "README.en.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(f"{v} %", "99.9 %"),
                          encoding="utf-8")
        assert any(f"{v} %" in p for p in estudio.compare_documents(root))

    def test_viewer_block_must_match(self):
        adj = json.loads((ROOT / "datos" / ajuste.ADJUSTMENT_FILE).read_text(encoding="utf-8"))
        good = {"calibrated": {"chosen": {"hectares": adj["chosen"]["hectares"]}}}
        assert estudio.compare_viewer_adjustment(good, adj) == []
        bad = {"calibrated": {"chosen": {"hectares": adj["chosen"]["hectares"] + 0.1}}}
        assert estudio.compare_viewer_adjustment(bad, adj)
        assert estudio.compare_viewer_adjustment({}, adj) == [
            "docs/index.html: DATA.calibrated missing or empty"]
        no_headline = {"calibrated": {"date": adj["date"]}}
        assert any("hectares missing" in p
                   for p in estudio.compare_viewer_adjustment(no_headline, adj))

    def test_published_viewer_block(self):
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        adj = json.loads((ROOT / "datos" / ajuste.ADJUSTMENT_FILE).read_text(encoding="utf-8"))
        data = estudio.viewer_data(html)
        assert data["calibrated"]["chosen"]["hectares"] == adj["chosen"]["hectares"]

    def test_changed_headline_in_viewer(self, tmp_path):
        """compare_documents must check the viewer's calibrated block, not only the README."""
        root = _repo(tmp_path)
        page = root / "docs" / "index.html"
        html = page.read_text(encoding="utf-8")
        adj = json.loads((root / "datos" / ajuste.ADJUSTMENT_FILE).read_text(encoding="utf-8"))
        old = f'"hectares":{adj["chosen"]["hectares"]}'
        assert old in html
        page.write_text(html.replace(old, '"hectares":9999.9', 1), encoding="utf-8")
        assert any("DATA.calibrated.chosen.hectares" in p for p in estudio.compare_documents(root))


# --- Files read by the case site ------------------------------------------------------------------

# nadir-observatorio.github.io/vina-del-mar-2024 links to the viewer and the factsheet and reads the
# calibrated figure from the adjustment file, checking it by this sha256.
SITE_FILES = ("docs/index.html", "ficha/ficha.pdf", "datos/ajuste_umbrales.json")
SITE_ADJUSTMENT_SHA256 = "9dd41007a46794bcd06450e3007da2033be978ff482ce0755ee807ccd920d8a9"
SITE_NOTICE = ("tell the maintainer of nadir-observatorio.github.io/vina-del-mar-2024 before "
               "publishing: its links break or its generator stops")


def test_files_read_by_the_case_site():
    """Moving, renaming or recomputing these files breaks the case site: this test stops it."""
    missing = [name for name in SITE_FILES if not (ROOT / name).is_file()]
    assert not missing, f"{missing} missing; {SITE_NOTICE}"
    digest = hashlib.sha256((ROOT / "datos" / ajuste.ADJUSTMENT_FILE).read_bytes()).hexdigest()
    assert digest == SITE_ADJUSTMENT_SHA256, f"{ajuste.ADJUSTMENT_FILE} changed; {SITE_NOTICE}"
