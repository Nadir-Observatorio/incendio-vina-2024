import csv

import numpy as np
import pytest

from severidad import contexto
from severidad.errors import DataError, SourceError


class TestDemTiles:
    def test_study_window_needs_two_tiles(self):
        """The window crosses 33 degrees south: tiles S34 and S33 of W072 (named by SW corner)."""
        assert contexto.dem_tiles((-71.65, -33.20, -71.28, -32.98)) == [
            "Copernicus_DSM_COG_10_S34_00_W072_00_DEM",
            "Copernicus_DSM_COG_10_S33_00_W072_00_DEM",
        ]

    def test_north_east_names(self):
        expected = ["Copernicus_DSM_COG_10_N00_00_E006_00_DEM"]
        assert contexto.dem_tiles((6.2, 0.1, 6.8, 0.9)) == expected

    def test_empty_box_raises(self):
        with pytest.raises(DataError):
            contexto.dem_tiles((-71.0, -33.0, -71.5, -32.0))

    def test_url_is_canonical(self):
        n = "Copernicus_DSM_COG_10_S34_00_W072_00_DEM"
        assert contexto.dem_url(n) == f"https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"


class TestHillshade:
    def test_flat_ground_is_sin_altitude(self):
        """Flat terrain is lit by sin(altitude) everywhere."""
        shade = contexto.hillshade(np.zeros((5, 5)), 20.0, altitude=45.0)
        assert np.allclose(shade, np.sin(np.radians(45.0)))

    def test_slope_facing_the_light_is_brighter(self):
        """With light from the north-west, a slope facing north-west is brighter than one facing
        south-east."""
        y, x = np.mgrid[0:20, 0:20].astype(float)
        facing_nw = (x + y) * 5.0          # rises to the south-east, so it faces north-west
        facing_se = -(x + y) * 5.0
        a = contexto.hillshade(facing_nw, 20.0)[10, 10]
        b = contexto.hillshade(facing_se, 20.0)[10, 10]
        assert a > b

    def test_nan_stays_nan(self):
        dem = np.zeros((5, 5))
        dem[2, 2] = np.nan
        assert np.isnan(contexto.hillshade(dem, 20.0)[2, 2])


class TestWaterClasses:
    def test_sea_touches_edge_and_lake_does_not(self):
        scl = np.full((10, 10), 4, dtype=np.uint8)
        scl[:, :3] = 6                     # sea along the west edge
        scl[5:7, 6:8] = 6                  # a reservoir inside
        out = contexto.water_classes(scl, scl)
        assert out[0, 0] == contexto.SEA
        assert out[5, 6] == contexto.INLAND
        assert out[0, 9] == contexto.LAND

    def test_water_must_be_water_in_both_scenes(self):
        """A pixel that is water in only one scene (cloud shadow, flood) is land."""
        pre = np.full((4, 4), 6, dtype=np.uint8)
        post = pre.copy()
        post[0, 0] = 4
        assert contexto.water_classes(pre, post)[0, 0] == contexto.LAND

    def test_different_grids_raise(self):
        with pytest.raises(DataError):
            contexto.water_classes(np.zeros((3, 3)), np.zeros((3, 4)))


class TestDownload:
    def test_records_and_never_overwrites(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"abc" * 1000)
        dest, registry = tmp_path / "out" / "f.bin", tmp_path / "out" / "registro.csv"
        contexto.download(src.as_uri(), dest, registry)
        assert dest.read_bytes() == src.read_bytes()
        src.write_bytes(b"changed")
        contexto.download(src.as_uri(), dest, registry)          # already present: untouched
        assert dest.read_bytes() == b"abc" * 1000
        rows = list(csv.DictReader(registry.open(encoding="utf-8")))
        assert len(rows) == 1 and rows[0]["sha256"] == contexto.sha256(dest)
        assert rows[0]["bytes"] == "3000"

    def test_failed_source_raises_source_error(self, tmp_path):
        missing = (tmp_path / "nope.bin").as_uri()
        with pytest.raises(SourceError):
            contexto.download(missing, tmp_path / "f.bin", tmp_path / "registro.csv")
        assert not (tmp_path / "f.bin").exists()

    def test_plain_http_is_refused(self, tmp_path):
        """Sources are fetched over https only; a plain http URL never reaches the network."""
        with pytest.raises(SourceError, match="non-https"):
            contexto.download("http://example.org/a.tif", tmp_path / "a.tif", tmp_path / "r.csv")
        assert not (tmp_path / "a.tif").exists()


class TestDerive:
    def test_refuses_to_overwrite(self, tmp_path):
        (tmp_path / "relieve.tif").write_bytes(b"x")
        with pytest.raises(FileExistsError):
            contexto.derive(tmp_path)


def _countries_file(tmp_path, codes, crs="EPSG:4326"):
    """Small Natural Earth-like shapefile zipped like the real download (numpy only)."""
    import zipfile

    from pyogrio.raw import write
    from shapely import to_wkb
    from shapely.geometry import box

    shp = tmp_path / "ne" / "ne_test.shp"
    shp.parent.mkdir()
    boxes = [to_wkb(box(-75 + i, -40, -74 + i, -30)) for i in range(len(codes))]
    geoms = np.array(boxes, dtype=object)
    fields = [np.array(codes, dtype=object), np.array([f"Name {c}" for c in codes], dtype=object)]
    write(str(shp), geoms, fields, ["ADM0_A3", "NAME"], geometry_type="Polygon", crs=crs,
          driver="ESRI Shapefile")
    zpath = tmp_path / "ne.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for f in shp.parent.iterdir():
            z.write(f, f.name)
    return zpath


class TestCountries:
    def test_reads_zip_without_geopandas(self, tmp_path):
        """The real download is a zipped shapefile; only Chile and its neighbours are kept."""
        src = _countries_file(tmp_path, ["CHL", "ARG", "BOL", "PER", "BRA"])
        fc = contexto.countries(src)
        codes = sorted(f["properties"]["ADM0_A3"] for f in fc["features"])
        assert codes == ["ARG", "BOL", "CHL", "PER"]
        assert fc["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")

    def test_missing_neighbour_raises(self, tmp_path):
        src = _countries_file(tmp_path, ["CHL", "ARG", "BOL"])
        with pytest.raises(DataError, match="PER"):
            contexto.countries(src)

    def test_projected_file_raises(self, tmp_path):
        """A locator drawn in lon/lat must not receive metres."""
        src = _countries_file(tmp_path, ["CHL", "ARG", "BOL", "PER"], crs="EPSG:32719")
        with pytest.raises(DataError, match="lon/lat"):
            contexto.countries(src)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(DataError):
            contexto.countries(tmp_path / "nope.zip")



def _geonames(tmp_path, rows, zipped=True):
    """GeoNames-like country extract: 19 tab-separated columns, inside CL.zip like the download."""
    import zipfile

    lines = []
    for gid, (name, lon, lat, fclass, pop) in enumerate(rows, 1):
        cols = [""] * 19
        cols[0], cols[1], cols[2] = str(gid), name, name
        cols[4], cols[5], cols[6], cols[8], cols[14] = str(lat), str(lon), fclass, "CL", str(pop)
        lines.append("\t".join(cols))
    text = "\n".join(lines) + "\n"
    if not zipped:
        path = tmp_path / "CL.txt"
        path.write_text(text, encoding="utf-8")
        return path
    path = tmp_path / "CL.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("CL.txt", text)
        z.writestr("readme.txt", "GeoNames")
    return path


class TestPlaces:
    BBOX = (-71.65, -33.20, -71.28, -32.98)
    ROWS = (("Viña del Mar", -71.55, -33.02, "P", 334248),
            ("Villa Alemana", -71.37, -33.04, "P", 126548),
            ("El Belloto", -71.40, -33.05, "P", 20000),        # below the population rule
            ("Cerro Alegre", -71.62, -33.04, "T", 90000),      # not a populated place (class T)
            ("Santiago", -70.65, -33.45, "P", 5000000))        # outside the window

    def test_population_class_and_window(self, tmp_path):
        fc = contexto.places(_geonames(tmp_path, self.ROWS), self.BBOX)
        names = [f["properties"]["NAME"] for f in fc["features"]]
        assert names == ["Villa Alemana", "Viña del Mar"]
        assert fc["features"][1]["geometry"]["coordinates"] == [-71.55, -33.02]

    def test_reads_the_unzipped_text_too(self, tmp_path):
        fc = contexto.places(_geonames(tmp_path, self.ROWS, zipped=False), self.BBOX)
        assert len(fc["features"]) == 2

    def test_rule_is_a_parameter(self, tmp_path):
        fc = contexto.places(_geonames(tmp_path, self.ROWS), self.BBOX, min_population=10_000)
        assert "El Belloto" in [f["properties"]["NAME"] for f in fc["features"]]

    def test_malformed_line_raises(self, tmp_path):
        """A changed file format must stop the derivation, not drop cities silently."""
        bad = tmp_path / "CL.txt"
        bad.write_text("1\tViña del Mar\t-33.0\n", encoding="utf-8")
        with pytest.raises(DataError, match="columns"):
            contexto.places(bad, self.BBOX)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(DataError):
            contexto.places(tmp_path / "CL.zip", self.BBOX)


class TestWorldCoverTiles:
    def test_study_window_needs_two_tiles(self):
        """The window crosses 33 degrees south: tiles S36 and S33 of W072 (3 x 3 degrees, named by
        their south-west corner on a grid aligned to multiples of 3)."""
        assert contexto.worldcover_tiles((-71.65, -33.20, -71.28, -32.98)) == ["S36W072",
                                                                               "S33W072"]

    def test_names_match_the_published_examples(self):
        """Tiles named in the WorldCover repository (N21E030, N33W093, N51E003)."""
        assert contexto.worldcover_tiles((30.5, 21.5, 31.0, 22.0)) == ["N21E030"]
        assert contexto.worldcover_tiles((-92.5, 34.0, -91.0, 35.0)) == ["N33W093"]
        assert contexto.worldcover_tiles((4.0, 52.0, 5.0, 53.0)) == ["N51E003"]

    def test_edge_belongs_to_the_northern_tile(self):
        """-33.0 is the south edge of S33, not part of S36."""
        assert contexto.worldcover_tiles((-71.6, -33.0, -71.5, -32.9)) == ["S33W072"]

    def test_crossing_a_meridian_adds_a_column(self):
        assert contexto.worldcover_tiles((-69.5, -33.5, -68.5, -33.4)) == ["S36W072", "S36W069"]

    def test_empty_box_raises(self):
        with pytest.raises(DataError):
            contexto.worldcover_tiles((-71.0, -33.0, -71.5, -32.0))

    def test_url_is_canonical(self):
        assert contexto.worldcover_url("S33W072") == (
            "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
            "ESA_WorldCover_10m_2021_v200_S33W072_Map.tif")


def _tile(path, west, north, classes, dtype="uint8"):
    """Small WorldCover-like tile in lon/lat (0.001 degree pixels)."""
    import rasterio
    from rasterio.transform import from_origin

    data = np.asarray(classes, dtype=dtype)
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1],
                       count=1, dtype=dtype, crs="EPSG:4326", nodata=0,
                       transform=from_origin(west, north, 0.001, 0.001)) as r:
        r.write(data, 1)
    return path


def _utm_grid(west_m, north_m, size, cell=20.0):
    from rasterio.transform import from_origin

    return {"height": size, "width": size, "crs": "EPSG:32719",
            "transform": from_origin(west_m, north_m, cell, cell)}


class TestBuiltUp:
    def test_mask_keeps_only_class_50(self):
        landcover = np.array([[10, 50, 80], [0, 50, 40]], dtype=np.uint8)
        assert contexto.built_mask(landcover).tolist() == [[0, 1, 0], [0, 1, 0]]
        assert contexto.built_mask(landcover).dtype == np.uint8

    def _two_tiles(self, tmp_path):
        """North tile from -32.90 to -33.00 (all tree cover, 10, with a built block); south tile
        from -33.00 to -33.10 (all grassland, 30, with another built block)."""
        north = np.full((100, 100), 10, dtype=np.uint8)
        north[80:100, 49:51] = 50                       # touches the -33 edge (~190 m wide)
        south = np.full((100, 100), 30, dtype=np.uint8)
        south[0:20, 49:51] = 50                         # continues it below the edge
        return [_tile(tmp_path / "S36.tif", -71.55, -33.00, south),
                _tile(tmp_path / "S33.tif", -71.55, -32.90, north)]

    def _grid_over(self):
        """UTM 19S grid, 20 m cells, centred on (-71.50, -33.00)."""
        from pyproj import Transformer

        x, y = Transformer.from_crs("EPSG:4326", "EPSG:32719", always_xy=True).transform(
            -71.50, -33.00)
        return _utm_grid(x - 1000.0, y + 1000.0, 100)

    def test_two_tiles_fill_the_grid_and_join_at_the_edge(self, tmp_path):
        """The second tile must not erase the first; the built block crosses the tile edge."""
        mask, gaps = contexto.built_on_grid(self._two_tiles(tmp_path), self._grid_over())
        assert gaps == 0
        assert mask[:45].any() and mask[55:].any()     # built on both sides of -33
        assert mask[50].any()                          # and continuous across it
        assert 0 < mask.mean() < 0.5

    def test_order_of_the_tiles_does_not_matter(self, tmp_path):
        tiles = self._two_tiles(tmp_path)
        a, _ = contexto.built_on_grid(tiles, self._grid_over())
        b, _ = contexto.built_on_grid(tiles[::-1], self._grid_over())
        assert np.array_equal(a, b)

    def test_pixels_without_worldcover_are_counted(self, tmp_path):
        """Only the south tile: the northern half of the grid has no data and is reported."""
        south = self._two_tiles(tmp_path)[:1]
        mask, gaps = contexto.built_on_grid(south, self._grid_over())
        assert 0.4 < gaps / mask.size < 0.6
        assert not mask[:45].any()

    def test_missing_tile_raises(self, tmp_path):
        with pytest.raises(DataError, match="descargar"):
            contexto.built_on_grid([tmp_path / "nope.tif"], self._grid_over())

    def test_tile_that_is_not_classes_raises(self, tmp_path):
        bad = _tile(tmp_path / "f.tif", -71.55, -32.90, np.zeros((10, 10)), dtype="float32")
        with pytest.raises(DataError, match="uint8"):
            contexto.built_on_grid([bad], self._grid_over())

    def test_derive_refuses_to_overwrite_the_new_layer(self, tmp_path):
        (tmp_path / "construido.tif").write_bytes(b"x")
        with pytest.raises(FileExistsError, match="construido"):
            contexto.derive(tmp_path)

    def test_attribution_is_the_official_2021_text(self):
        assert contexto.ATTRIBUTION["worldcover"] == (
            "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data "
            "(2021) processed by ESA WorldCover consortium")
