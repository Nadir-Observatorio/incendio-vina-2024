import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from severidad import web

T = from_origin(260000, 6350000, 20, 20)   # UTM 19S, near Vina del Mar
CRS = "EPSG:32719"


class TestRgba:
    def test_unburned_is_transparent(self):
        """Unburned and no data must not cover the base map."""
        img = web.rgba(np.array([[0, 3, 7]], dtype=np.uint8))
        assert img[0, 0, 3] == 0 and img[0, 1, 3] == 0 and img[0, 2, 3] > 0

    def test_unknown_code_raises(self):
        """A class without a colour must fail instead of being drawn transparent."""
        with pytest.raises(ValueError, match="without colour"):
            web.rgba(np.array([[9]], dtype=np.uint8))


class TestToWebMercator:
    def test_keeps_class_codes(self):
        """Nearest neighbour must never invent codes between two classes."""
        classes = np.zeros((50, 60), dtype=np.uint8)
        classes[:, :30], classes[:, 30:] = 4, 7
        out, _ = web.to_web_mercator(classes, T, CRS)
        assert set(np.unique(out).tolist()) <= {0, 4, 7}

    def test_keeps_ground_resolution(self):
        """The web image must keep about 20 m on the ground, not a coarser pixel."""
        _, t = web.to_web_mercator(np.ones((50, 50), dtype=np.uint8), T, CRS)
        assert abs(t.a) * np.cos(np.radians(-33.0)) == pytest.approx(20.0, rel=0.1)

    def test_preserves_burned_area(self):
        """The warped burned pixels must cover about the same ground (Mercator scale corrected)."""
        classes = np.zeros((100, 100), dtype=np.uint8)
        classes[20:80, 20:80] = 6
        out, t = web.to_web_mercator(classes, T, CRS)
        lat = np.radians(-33.0)
        ground_m2 = (out == 6).sum() * abs(t.a * t.e) * np.cos(lat) ** 2
        assert ground_m2 == pytest.approx(60 * 60 * 400, rel=0.03)


class TestCorners:
    def test_order_and_orientation(self):
        """Corners must go top-left, top-right, bottom-right, bottom-left (MapLibre order)."""
        classes = np.ones((10, 20), dtype=np.uint8)
        out, t = web.to_web_mercator(classes, T, CRS)
        (tl, tr, br, bl) = web.corners(t, out.shape)
        assert tl[0] < tr[0] and tl[1] > bl[1] and br[0] == tr[0] and br[1] == bl[1]

    def test_corners_match_source_location(self):
        """The image must land where the UTM raster is (within one pixel)."""
        classes = np.ones((10, 10), dtype=np.uint8)
        out, t = web.to_web_mercator(classes, T, CRS)
        tl = web.corners(t, out.shape)[0]
        from pyproj import Transformer
        lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(260000, 6350000)
        assert tl == pytest.approx([lon, lat], abs=3e-4)


class TestWritePng:
    def test_refuses_to_overwrite(self, tmp_path):
        """An existing PNG must not be replaced silently."""
        tif = tmp_path / "c.tif"
        with rasterio.open(tif, "w", driver="GTiff", width=4, height=4, count=1, dtype="uint8",
                           crs=CRS, transform=T, nodata=0) as r:
            r.write(np.full((4, 4), 5, dtype=np.uint8), 1)
        png = tmp_path / "c.png"
        web.write_png(tif, png)
        with pytest.raises(FileExistsError):
            web.write_png(tif, png)
