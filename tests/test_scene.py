import math
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from osgeo import gdal

from viz import gridmath, paths, scene

gdal.UseExceptions()

# A one-degree square: top-left (-100, 40), top-right (-99, 40), bottom-right (-99, 39), bottom-left (-100, 39).
CORNERS = [[-100.0, 40.0], [-99.0, 40.0], [-99.0, 39.0], [-100.0, 39.0]]


def write_png(path, bands=3):
    """A 4x4 image, mid-grey except a pure red top-left pixel and a pure green top-right pixel."""
    ds = gdal.GetDriverByName("MEM").Create("", 4, 4, bands, gdal.GDT_Byte)
    for index in range(bands):
        data = np.full((4, 4), 128, dtype=np.uint8)
        if bands >= 3:
            data[0, 0] = 255 if index == 0 else 0
            data[0, 3] = 255 if index == 1 else 0
        else:
            data[0, 0] = 255
        ds.GetRasterBand(index + 1).WriteArray(data)
    gdal.GetDriverByName("PNG").CreateCopy(str(path), ds)


def pixel_of(lon, lat, z, x, y):
    """Pixel (col, row) of a lon/lat inside an XYZ tile."""
    n = 2 ** z
    px = ((lon + 180.0) / 360.0 * n - x) * gridmath.TILE_SIZE
    py = ((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n - y) * gridmath.TILE_SIZE
    return int(px), int(py)


def decode(blob):
    name = "/vsimem/decode_test.png"
    gdal.FileFromMemBuffer(name, blob)
    try:
        ds = gdal.Open(name)
        return ds.ReadAsArray()          # (bands, rows, cols)
    finally:
        gdal.Unlink(name)


class SceneTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.saved = (paths.SCENE_CACHE, paths.TILE_CACHE)
        paths.SCENE_CACHE = self.tmp / "emit"
        paths.TILE_CACHE = self.tmp / "tiles"
        self.addCleanup(self._restore)
        scene.forget_all()

    def _restore(self):
        paths.SCENE_CACHE, paths.TILE_CACHE = self.saved
        scene.forget_all()


class TestEnsureBrowse(SceneTestCase):
    def test_downloads_once_through_a_part_file(self):
        calls = []

        def fake(url, dest):
            calls.append((url, str(dest)))
            write_png(dest)

        url = "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-public/x/S1.png"
        path = scene.ensure_browse("S1", url, fetch=fake)
        self.assertEqual(path, paths.SCENE_CACHE / "S1.png")
        self.assertTrue(path.is_file())
        self.assertTrue(calls[0][1].endswith(".part"))
        self.assertFalse(list(paths.SCENE_CACHE.glob("*.part")))
        scene.ensure_browse("S1", url, fetch=fake)
        self.assertEqual(len(calls), 1)

    def test_refuses_other_hosts_and_missing_urls_before_fetching(self):
        def fake(url, dest):
            raise AssertionError("must not be called")

        with self.assertRaises(scene.BrowseError):
            scene.ensure_browse("S2", "https://example.com/x.png", fetch=fake)
        with self.assertRaises(scene.BrowseError):
            scene.ensure_browse("S3", None, fetch=fake)

    def test_stale_part_file_is_not_trusted(self):
        paths.SCENE_CACHE.mkdir(parents=True)
        (paths.SCENE_CACHE / "S4.png.part").write_bytes(b"junk")
        calls = []

        def fake(url, dest):
            calls.append(url)
            write_png(dest)

        scene.ensure_browse("S4", "https://data.lpdaac.earthdatacloud.nasa.gov/x/S4.png", fetch=fake)
        self.assertEqual(len(calls), 1)
        self.assertTrue((paths.SCENE_CACHE / "S4.png").is_file())

    def test_fetch_failure_is_a_browse_error_and_leaves_no_file(self):
        def fake(url, dest):
            raise OSError("timed out")

        with self.assertRaises(scene.BrowseError):
            scene.ensure_browse("S5", "https://data.lpdaac.earthdatacloud.nasa.gov/x/S5.png", fetch=fake)
        self.assertFalse((paths.SCENE_CACHE / "S5.png").exists())

    def test_non_image_bytes_are_rejected_and_not_cached(self):
        def fake(url, dest):
            Path(dest).write_bytes(b"<html>Earthdata Login</html>")

        with self.assertRaises(scene.BrowseError):
            scene.ensure_browse("S6", "https://data.lpdaac.earthdatacloud.nasa.gov/x/S6.png", fetch=fake)
        self.assertFalse((paths.SCENE_CACHE / "S6.png").exists())
        self.assertFalse(list(paths.SCENE_CACHE.glob("*.part")))

    def test_incomplete_read_through_download_is_a_browse_error(self):
        import http.client

        with patch.object(scene.urllib.request, "urlopen", side_effect=http.client.IncompleteRead(b"")):
            with self.assertRaises(scene.BrowseError):
                scene.download("https://data.lpdaac.earthdatacloud.nasa.gov/x/S7.png",
                                self.tmp / "S7.png.part")


class TestRenderTile(SceneTestCase):
    def setUp(self):
        super().setUp()
        paths.SCENE_CACHE.mkdir(parents=True)
        write_png(scene.browse_path("S1"))
        write_png(scene.browse_path("G1"), bands=1)

    def test_corner_pixel_lands_at_its_vertex_and_outside_is_transparent(self):
        z = 8

        x, y = gridmath.lonlat_to_tile(-99.9, 39.9, z)     # inside the top-left source pixel
        rgba = decode(scene.render_tile("S1", CORNERS, z, x, y))
        self.assertEqual(rgba.shape[0], 4)
        col, row = pixel_of(-99.9, 39.9, z, x, y)
        self.assertGreater(rgba[0, row, col], 200)
        self.assertLess(rgba[1, row, col], 60)
        self.assertEqual(rgba[3, row, col], 255)

        x, y = gridmath.lonlat_to_tile(-99.1, 39.9, z)     # inside the top-right source pixel: corners[1]
        rgba = decode(scene.render_tile("S1", CORNERS, z, x, y))
        col, row = pixel_of(-99.1, 39.9, z, x, y)
        self.assertLess(rgba[0, row, col], 60)
        self.assertGreater(rgba[1, row, col], 200)
        self.assertEqual(rgba[3, row, col], 255)

        x, y = gridmath.lonlat_to_tile(-99.1, 39.1, z)     # bottom-right source pixel: grey
        rgba = decode(scene.render_tile("S1", CORNERS, z, x, y))
        col, row = pixel_of(-99.1, 39.1, z, x, y)
        self.assertTrue(100 <= rgba[0, row, col] <= 160)

        x, y = gridmath.lonlat_to_tile(-100.4, 40.4, z)    # outside the quad
        rgba = decode(scene.render_tile("S1", CORNERS, z, x, y))
        col, row = pixel_of(-100.4, 40.4, z, x, y)
        self.assertEqual(rgba[3, row, col], 0)

    def test_single_band_source_renders_grey(self):
        z = 8
        x, y = gridmath.lonlat_to_tile(-99.5, 39.5, z)
        rgba = decode(scene.render_tile("G1", CORNERS, z, x, y))
        col, row = pixel_of(-99.5, 39.5, z, x, y)
        self.assertEqual(int(rgba[0, row, col]), int(rgba[1, row, col]))
        self.assertEqual(int(rgba[1, row, col]), int(rgba[2, row, col]))

    def test_far_tile_is_the_shared_transparent_tile_and_is_cached(self):
        blob = scene.render_tile("S1", CORNERS, 8, 10, 10)
        self.assertEqual(blob, scene.transparent_tile())
        self.assertFalse((paths.TILE_CACHE / "emit-S1").exists())
        near = scene.render_tile("S1", CORNERS, 8, *gridmath.lonlat_to_tile(-99.5, 39.5, 8))
        self.assertNotEqual(near, blob)
        self.assertTrue(list((paths.TILE_CACHE / "emit-S1").rglob("*.png")))

    def test_tile_lonlat_bounds(self):
        west, south, east, north = scene.tile_lonlat_bounds(1, 0, 0)
        self.assertAlmostEqual(west, -180.0, places=6)
        self.assertAlmostEqual(east, 0.0, places=6)
        self.assertAlmostEqual(south, 0.0, places=6)
        self.assertAlmostEqual(north, 85.0511, places=3)

    def test_concurrent_gcp_source_builds_once(self):
        real_translate = scene.gdal.Translate
        calls = []

        def counting_translate(*args, **kwargs):
            calls.append(1)
            return real_translate(*args, **kwargs)

        names = [None] * 8
        barrier = threading.Barrier(8)

        def worker(index):
            barrier.wait()
            names[index] = scene.gcp_source("S1", CORNERS)

        with patch.object(scene.gdal, "Translate", side_effect=counting_translate):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertTrue(all(name == names[0] for name in names))
        self.assertEqual(len(calls), 2)  # one GeoTIFF translate, one GCP VRT translate; built once, not per thread

    def test_forget_all_unlinks_both_vsimem_files(self):
        scene.gcp_source("S1", CORNERS)
        self.assertTrue(any("scene_S1" in e for e in gdal.ReadDirRecursive("/vsimem/") or []))
        scene.forget_all()
        entries = gdal.ReadDirRecursive("/vsimem/") or []
        self.assertFalse([e for e in entries if "scene_" in e])


if __name__ == "__main__":
    unittest.main()
