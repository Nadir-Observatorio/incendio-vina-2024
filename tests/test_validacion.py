import hashlib
import io
import json
import zipfile

import numpy as np
import pytest
from rasterio.transform import from_origin

from severidad import validacion as v
from severidad.errors import DataError


class TestWilson:
    def test_known_interval(self):
        """164 of 300 must give 49.0 to 60.2 % (value published for measurement 1)."""
        p = v.wilson(164, 300)
        assert (round(100 * p.low, 1), round(100 * p.high, 1)) == (49.0, 60.2)

    def test_exact_edges(self):
        """0 and n successes must give exactly 0 and 1, not floating-point noise."""
        assert v.wilson(0, 10).low == 0.0 and v.wilson(10, 10).high == 1.0

    def test_rejects_empty_sample(self):
        """An interval without observations must not be invented."""
        with pytest.raises(ValueError):
            v.wilson(0, 0)


class TestSamplePoints:
    def test_keeps_minimum_distance(self):
        """No two kept points may be closer than the declared distance."""
        xy = np.random.default_rng(1).uniform(0, 1000, size=(500, 2))
        kept = xy[v.sample_points(xy, 50, 100.0, 7)]
        d = np.hypot(*(kept[:, None, :] - kept[None, :, :]).transpose(2, 0, 1))
        assert d[np.triu_indices(len(kept), 1)].min() >= 100.0

    def test_same_seed_same_sample(self):
        """The sample must depend only on the seed."""
        xy = np.random.default_rng(2).uniform(0, 1000, size=(200, 2))
        assert np.array_equal(v.sample_points(xy, 20, 50.0, 3), v.sample_points(xy, 20, 50.0, 3))


class TestSampleStrata:
    def test_short_stratum_raises(self):
        """A stratum that cannot reach n must fail instead of returning a smaller sample."""
        predicted = np.zeros((20, 20), dtype=bool)
        predicted[:2, :2] = True
        with pytest.raises(ValueError, match="burned stratum"):
            v.sample_strata(predicted, np.ones_like(predicted), 5, 1, 0)

    def test_chebyshev_distance(self):
        """Kept pixels must be at least min_distance_px apart in rows or columns."""
        predicted = np.zeros((60, 60), dtype=bool)
        predicted[:, :30] = True
        burned, _ = v.sample_strata(predicted, np.ones_like(predicted), 9, 4, 10)
        rc = np.array([divmod(int(i), 60) for i in burned])
        d = np.abs(rc[:, None, :] - rc[None, :, :]).max(axis=2)
        assert d[np.triu_indices(len(rc), 1)].min() >= 10

    def test_ignores_invalid_pixels(self):
        """Pixels outside the domain must never be sampled."""
        predicted = np.zeros((30, 30), dtype=bool)
        predicted[:, :15] = True
        valid = np.ones_like(predicted)
        valid[::2] = False
        b, u = v.sample_strata(predicted, valid, 20, 5, 0)
        assert valid.ravel()[np.concatenate([b, u])].all()


class TestAgreement:
    def test_perfect_prediction(self):
        """Prediction equal to the reference must give accuracy 1 and no commission."""
        predicted = np.zeros((40, 40), dtype=bool)
        predicted[:, :10] = True
        a = v.agreement(predicted, predicted.copy(), np.ones_like(predicted), 30, 1)
        assert (a.overall.value, a.weighted_accuracy, a.commission.successes,
                a.weighted_omission) == (1.0, 1.0, 0, 0.0)

    def test_weights_by_stratum_area(self):
        """Weighted accuracy must weight each stratum by its share of the domain (1/4 burned)."""
        predicted = np.zeros((40, 40), dtype=bool)
        predicted[:, :10] = True
        reference = np.zeros_like(predicted)  # every predicted-burned pixel is commission
        a = v.agreement(predicted, reference, np.ones_like(predicted), 30, 1)
        assert a.matrix == ((0, 30), (0, 30)) and a.weighted_accuracy == pytest.approx(0.75)

    def test_omission_from_unburned_stratum(self):
        """Reference burned area missed by the prediction must appear as weighted omission."""
        predicted = np.zeros((40, 40), dtype=bool)
        predicted[:, :20] = True
        reference = np.ones_like(predicted)
        a = v.agreement(predicted, reference, np.ones_like(predicted), 30, 1)
        assert a.weighted_omission == pytest.approx(0.5)


class TestHelpers:
    def test_values_at_outside(self):
        """A point outside the raster must be flagged and read as 0, not wrap around."""
        raster = np.arange(1, 5, dtype=np.uint8).reshape(2, 2)
        t = from_origin(0, 20, 10, 10)
        values, inside = v.values_at(np.array([[5.0, 15.0], [-5.0, 5.0]]), raster, t)
        assert values.tolist() == [1, 0] and inside.tolist() == [True, False]

    def test_check_file_detects_change(self, tmp_path):
        """A reference whose content changed must be rejected even with the same size."""
        f = tmp_path / "ref.zip"
        f.write_bytes(b"abcd")
        v.check_file(f, 4, hashlib.sha256(b"abcd").hexdigest())
        with pytest.raises(ValueError, match="sha256"):
            v.check_file(f, 4, hashlib.sha256(b"abce").hexdigest())

    def test_inner_zip_rejects_divergent_copies(self, tmp_path):
        """Two inner ZIPs with the same name and different content make the reference ambiguous."""
        outer = tmp_path / "products.zip"
        with zipfile.ZipFile(outer, "w") as z, pytest.warns(UserWarning):
            z.writestr("X_v2.zip", b"one")
            z.writestr("X_v2.zip", b"two")
        with pytest.raises(ValueError, match="different contents"):
            v._inner_zip(outer, "X_v2.zip")

    def test_inner_sha256_of_repackaged_product(self, tmp_path):
        """A repackaged outer ZIP must still identify its inner product by content."""
        outer = tmp_path / "products.zip"
        with zipfile.ZipFile(outer, "w") as z:
            z.writestr("X_v2.zip", b"product")
        assert v.inner_sha256(outer, "X_v2.zip") == hashlib.sha256(b"product").hexdigest()

    def test_delineation_filters_post_source(self, tmp_path):
        """Only footprints of the post-event image (or_src_id 2) must be kept."""
        square = {"type": "Polygon", "coordinates": [[[-71.5, -33.0], [-71.49, -33.0],
                                                     [-71.49, -33.01], [-71.5, -33.01],
                                                     [-71.5, -33.0]]]}
        def fc(props):
            return {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": p, "geometry": square} for p in props]}

        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("P_observedEventA_v2.json", json.dumps(fc([{}])))
            z.writestr("P_imageFootprintA_v2.json",
                       json.dumps(fc([{"or_src_id": 1}, {"or_src_id": 2}])))
        outer = tmp_path / "products.zip"
        with zipfile.ZipFile(outer, "w") as z:
            z.writestr("P_v2.zip", inner.getvalue())
        burned, footprint = v.emsr715_delineation(outer, "EPSG:32719", product="P")
        assert len(burned) == 1 and len(footprint) == 1


def _emsn188_package(tmp_path, crs="EPSG:32719"):
    """A zip laid out like EMSN188_GeospatialData.zip with a small file geodatabase inside."""
    pyogrio = pytest.importorskip("pyogrio")
    from shapely import Point, to_wkb

    gdb = tmp_path / "build" / v.EMSN188_GDB
    gdb.parent.mkdir(parents=True)
    points = [Point(260010.0, 6349990.0), Point(260050.0, 6349950.0), Point(260090.0, 6349850.0)]
    grades = np.array(["3", "2", "1"], dtype=object)
    pyogrio.raw.write(str(gdb), geometry=np.array([to_wkb(p) for p in points], dtype=object),
                      field_data=[grades], fields=["damage_gra"], geometry_type="Point",
                      crs=crs, driver="OpenFileGDB", layer=v.EMSN188_POINTS)
    package = tmp_path / "EMSN188_GeospatialData.zip"
    with zipfile.ZipFile(package, "w") as z:
        for f in gdb.rglob("*"):
            z.write(f, f.relative_to(tmp_path / "build").as_posix())
    return package


class TestEmsn188:
    def test_reads_points_and_grades(self, tmp_path):
        """Damage points must come out in the map CRS with their grade codes, in file order."""
        xy, grades = v.emsn188_damage(_emsn188_package(tmp_path), "EPSG:32719")
        assert xy.tolist() == [[260010.0, 6349990.0], [260050.0, 6349950.0],
                               [260090.0, 6349850.0]]
        assert grades.tolist() == ["3", "2", "1"]
        assert set(grades.tolist()) == set(v.DAMAGE_GRADES)

    def test_other_crs_is_rejected(self, tmp_path):
        """Points in another CRS must not be compared with the map pixel by pixel."""
        with pytest.raises(DataError, match="EMSN188 points are in"):
            v.emsn188_damage(_emsn188_package(tmp_path, crs="EPSG:4326"), "EPSG:32719")
