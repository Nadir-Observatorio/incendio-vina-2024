"""Tests of the source module: Esri JSON to GeoJSON, and downloads with the network replaced
by fakes (these tests never connect)."""
from __future__ import annotations

import io
import urllib.error

import pytest
from shapely.geometry import shape

from severidad import fuente
from severidad.errors import SourceError
from severidad.fuente import esri_to_geojson


def test_outer_ring_with_hole() -> None:
    """A clockwise outer ring with a counter-clockwise hole becomes one polygon with a hole."""
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]          # clockwise
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]               # counter-clockwise
    g = shape(esri_to_geojson({"rings": [outer, hole]}))
    assert g.geom_type == "MultiPolygon" and len(g.geoms) == 1
    assert g.area == 100 - 4


def test_two_outer_rings() -> None:
    """Two separate clockwise rings (an island) become two polygons."""
    a = [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]
    b = [[5, 5], [5, 6], [6, 6], [6, 5], [5, 5]]
    assert len(shape(esri_to_geojson({"rings": [a, b]})).geoms) == 2


# --- Downloads ------------------------------------------------------------------------------------


class _Response(io.BytesIO):
    """Stand-in for an HTTPS response: bytes plus a Content-Length header."""

    def __init__(self, body: bytes, length: int | None = None):
        super().__init__(body)
        self.headers = {} if length is None else {"Content-Length": str(length)}


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Retries must never make the suite wait for real."""
    monkeypatch.setattr(fuente, "_sleep", lambda seconds: None)


def _item(*assets: str) -> dict:
    return {"assets": {a: {"href": f"https://example.org/{a}.tif"} for a in assets}}


@pytest.fixture
def network(monkeypatch):
    """Records every address opened and serves the bodies given in `network.bodies`."""
    class Network:
        def __init__(self):
            self.opened: list[str] = []
            self.bodies: dict[str, tuple[bytes, int | None]] = {}
            self.catalogue = _item("nir08", "swir22", "scl", "visual", "red")

    net = Network()

    def fake_open(url, timeout):
        net.opened.append(url)
        body, length = net.bodies.get(url, (b"pixels", None))
        return _Response(body, len(body) if length is None else length)

    monkeypatch.setattr(fuente, "_open", fake_open)
    monkeypatch.setattr(fuente, "item", lambda scene_id, timeout=60.0: net.catalogue)
    return net


class TestDownload:
    def test_only_requested_bands_are_downloaded(self, network, tmp_path):
        """Asking for the colour composite must download it alone, not the study bands."""
        paths = fuente.download_scene("S", tmp_path, bands=fuente.COLOUR_BANDS)
        assert list(paths) == ["visual"]
        assert network.opened == ["https://example.org/visual.tif"]
        assert (tmp_path / "S" / "visual.tif").read_bytes() == b"pixels"

    def test_default_bands_are_the_study_bands(self, network, tmp_path):
        """Without `bands`, the three bands of the dNBR must be downloaded, as before."""
        assert list(fuente.download_scene("S", tmp_path)) == list(fuente.BANDS)
        assert len(network.opened) == 3

    def test_present_file_is_kept_without_network(self, network, tmp_path, monkeypatch):
        """An existing band must not be downloaded again nor the catalogue asked."""
        (tmp_path / "S").mkdir()
        (tmp_path / "S" / "visual.tif").write_bytes(b"old")

        def no_catalogue(scene_id, timeout=60.0):
            raise AssertionError("catalogue asked")

        monkeypatch.setattr(fuente, "item", no_catalogue)
        fuente.download_scene("S", tmp_path, bands=["visual"])
        assert network.opened == [] and (tmp_path / "S" / "visual.tif").read_bytes() == b"old"

    def test_cut_download_is_kept_as_part(self, network, tmp_path):
        """A body shorter than Content-Length must raise and never become a band file."""
        network.bodies["https://example.org/visual.tif"] = (b"pix", 6)
        with pytest.raises(SourceError, match="received 3 of 6 bytes"):
            fuente.download_scene("S", tmp_path, bands=["visual"])
        assert not (tmp_path / "S" / "visual.tif").exists()
        assert (tmp_path / "S" / "visual.tif.part").read_bytes() == b"pix"

    def test_unknown_asset_is_reported_before_downloading(self, network, tmp_path):
        """A band name the catalogue does not have must fail with the names it does have."""
        with pytest.raises(SourceError, match=r"no asset \['blue'\].*'visual'"):
            fuente.download_scene("S", tmp_path, bands=["visual", "blue"])
        assert network.opened == []

    @pytest.mark.parametrize("bands", [[], ["visual", "visual"]])
    def test_bad_band_list(self, network, tmp_path, bands):
        """An empty or repeated band list is a mistake, not a request."""
        with pytest.raises(SourceError, match="non-empty"):
            fuente.download_scene("S", tmp_path, bands=bands)


class TestOpen:
    def test_plain_http_is_refused(self, monkeypatch):
        """Only https addresses may be opened; the check happens before connecting."""
        def no_network(*args, **kwargs):
            raise AssertionError("connected")

        monkeypatch.setattr(fuente.urllib.request, "urlopen", no_network)
        for url in ("http://example.org/a.tif", "file:///etc/passwd"):
            with pytest.raises(SourceError, match="only https"):
                fuente._open(url, 1.0)

    def test_network_failure_becomes_source_error(self, monkeypatch):
        """A failed connection must reach the command line as SourceError (exit code 2)."""
        def refused(url, timeout):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(fuente.urllib.request, "urlopen", refused)
        with pytest.raises(SourceError, match="refused"):
            fuente._open("https://example.org/a", 1.0)

    def test_json_must_be_an_object(self, monkeypatch):
        """A catalogue answer that is not a JSON object must be rejected."""
        monkeypatch.setattr(fuente, "_open", lambda url, timeout: _Response(b"[1, 2]"))
        with pytest.raises(SourceError, match="expected a JSON object"):
            fuente.item("S")


class TestRetries:
    @pytest.fixture
    def waits(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(fuente, "_sleep", slept.append)
        return slept

    def _urlopen(self, monkeypatch, outcomes):
        calls = []

        def fake(url, timeout):
            calls.append(url)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(fuente.urllib.request, "urlopen", fake)
        return calls

    def test_transient_failure_is_retried(self, monkeypatch, waits):
        """A dropped connection followed by a good answer must succeed after one wait of 2 s."""
        ok = _Response(b"{}")
        calls = self._urlopen(monkeypatch, [urllib.error.URLError("reset"), ok])
        assert fuente._open("https://example.org/a", 1.0) is ok
        assert len(calls) == 2 and waits == [2.0]

    def test_gives_up_after_three_attempts(self, monkeypatch, waits):
        """Three time-outs must end in SourceError after waits of 2 and 4 s, not loop forever."""
        calls = self._urlopen(monkeypatch, [TimeoutError("slow")] * 3)
        with pytest.raises(SourceError, match="attempt 3 of 3"):
            fuente._open("https://example.org/a", 1.0)
        assert len(calls) == 3 and waits == [2.0, 4.0]

    @pytest.mark.parametrize(("code", "tries"), [(404, 1), (403, 1), (503, 3), (429, 3)])
    def test_http_errors(self, monkeypatch, waits, code, tries):
        """A client error (404) fails at once; a server error (503) or a 429 is retried."""
        error = urllib.error.HTTPError("https://example.org/a", code, "x", None, None)
        calls = self._urlopen(monkeypatch, [error] * 3)
        with pytest.raises(SourceError):
            fuente._open("https://example.org/a", 1.0)
        assert len(calls) == tries

    def test_connection_lost_while_downloading(self, monkeypatch, tmp_path):
        """A connection cut mid-band must raise SourceError and leave only the .part file."""
        class Cut(_Response):
            def read(self, size=-1):
                if self.tell():
                    raise ConnectionResetError("reset by peer")
                return super().read(3)

        monkeypatch.setattr(fuente, "item", lambda scene_id, timeout=60.0: _item("visual"))
        monkeypatch.setattr(fuente, "_open", lambda url, timeout: Cut(b"pixels", 6))
        with pytest.raises(SourceError, match="connection lost after 3 bytes"):
            fuente.download_scene("S", tmp_path, bands=["visual"])
        assert not (tmp_path / "S" / "visual.tif").exists()
        assert (tmp_path / "S" / "visual.tif.part").read_bytes() == b"pix"


class TestMunicipalities:
    RING = ((0, 0), (0, 1), (1, 1), (1, 0), (0, 0))

    def _serve(self, monkeypatch, answer):
        seen = []

        def fake_json(url, timeout):
            seen.append(url)
            return answer

        monkeypatch.setattr(fuente, "_json", fake_json)
        return seen

    def test_features_with_padded_codes(self, monkeypatch):
        """Codes returned as numbers must be padded to 5 digits and the query must be https."""
        feature = {"attributes": {"CUT_COM": 5101, "COMUNA": "A"},
                   "geometry": {"rings": [list(self.RING)]}}
        seen = self._serve(monkeypatch, {"features": [feature]})
        fc = fuente.municipalities({"05101": "A"})
        assert fc["features"][0]["properties"] == {"CUT_COM": "05101", "COMUNA": "A"}
        assert seen[0].startswith(fuente.MUNICIPALITIES + "?") and "f=json" in seen[0]

    def test_service_error_member(self, monkeypatch):
        """ArcGIS errors arrive with HTTP 200 and an "error" member; they must raise."""
        self._serve(monkeypatch, {"error": {"code": 400}})
        with pytest.raises(SourceError, match="municipality service error"):
            fuente.municipalities({"05101": "A"})

    def test_missing_municipality(self, monkeypatch):
        """A requested municipality absent from the answer must raise, naming it."""
        self._serve(monkeypatch, {"features": []})
        with pytest.raises(SourceError, match="05101"):
            fuente.municipalities({"05101": "A"})
