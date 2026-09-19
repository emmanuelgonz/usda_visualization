import shutil
import struct
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal

from tests import fixtures
from viz import gridmath, rasters


class TestOpenCached(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = str(fixtures.write_cdl_like(self.tmp / "cdl.tif"))

    def test_returns_the_same_handle_within_one_thread(self):
        self.assertIs(rasters.open_cached(self.path), rasters.open_cached(self.path))

    def test_each_thread_gets_its_own_handle(self):
        main = rasters.open_cached(self.path)
        other = {}

        def worker():
            other["ds"] = rasters.open_cached(self.path)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertIsNot(main, other["ds"])

    def test_missing_file_raises(self):
        with self.assertRaises(Exception):
            rasters.open_cached(str(self.tmp / "absent.tif"))


class TestWarpTile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cdl = str(fixtures.write_cdl_like(self.tmp / "cdl.tif"))
        self.cpc = str(fixtures.write_cpc_like(self.tmp / "cpc.tif"))

    def test_returns_a_256_square_array(self):
        z, x, y = fixtures.tile_covering(self.cdl)
        out = rasters.warp_tile(self.cdl, z, x, y, resample="near")
        self.assertEqual(out.shape, (gridmath.TILE_SIZE, gridmath.TILE_SIZE))

    def test_thematic_warp_preserves_exact_class_codes(self):
        z, x, y = fixtures.tile_covering(self.cdl)
        out = rasters.warp_tile(self.cdl, z, x, y, resample="near")
        self.assertTrue(set(np.unique(out)).issubset({0, 1}))

    def test_continuous_warp_returns_float(self):
        z, x, y = fixtures.tile_covering(self.cpc)
        out = rasters.warp_tile(self.cpc, z, x, y, resample="bilinear", dtype="float32")
        self.assertEqual(out.dtype, np.float32)

    def test_multiband_request_returns_band_major_array(self):
        path = str(self.tmp / "two.tif")
        rasters.write_multiband_float(path, np.zeros((2, 30, 40), dtype=np.float32),
                                      gridmath.CPC_GRID)
        z, x, y = fixtures.tile_covering(path)
        out = rasters.warp_tile(path, z, x, y, resample="bilinear", bands=[1, 2],
                                dtype="float32")
        self.assertEqual(out.shape, (2, gridmath.TILE_SIZE, gridmath.TILE_SIZE))

    def test_thematic_warp_actually_reads_the_fixture(self):
        # Guards against a tile that misses the data and passes vacuously.
        z, x, y = fixtures.tile_covering(self.cdl)
        out = rasters.warp_tile(self.cdl, z, x, y, resample="near")
        self.assertGreater(int((out == 1).sum()), 0)

    def test_tile_far_from_the_data_is_all_nodata(self):
        z, x, y = fixtures.tile_far_from(self.cdl)
        out = rasters.warp_tile(self.cdl, z, x, y, resample="near")
        self.assertTrue((out == 0).all())

    def test_multiband_source_without_explicit_bands_raises(self):
        path = str(self.tmp / "two.tif")
        rasters.write_multiband_float(path, np.zeros((2, 30, 40), dtype=np.float32),
                                      gridmath.CPC_GRID)
        z, x, y = fixtures.tile_covering(path)
        with self.assertRaises(ValueError):
            rasters.warp_tile(path, z, x, y, resample="bilinear", dtype="float32")


class TestSamplePoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cpc = str(fixtures.write_cpc_like(self.tmp / "cpc.tif", fill=3.75))

    def test_reads_the_value_at_a_projected_coordinate(self):
        x = gridmath.CPC_GRID["origin_x"] + 5 * gridmath.CPC_GRID["pixel_x"]
        y = gridmath.CPC_GRID["origin_y"] + 5 * gridmath.CPC_GRID["pixel_y"]
        self.assertAlmostEqual(rasters.sample_point(self.cpc, x, y)[0], 3.75, places=4)

    def test_point_outside_the_raster_returns_none(self):
        self.assertIsNone(rasters.sample_point(self.cpc, 0.0, 0.0)[0])

    def test_point_half_a_pixel_west_of_the_origin_returns_none(self):
        # int() truncation toward zero would map this to column 0 and pass the
        # bounds check; math.floor must reject it instead.
        x = gridmath.CPC_GRID["origin_x"] - 0.5 * gridmath.CPC_GRID["pixel_x"]
        y = gridmath.CPC_GRID["origin_y"] + 5 * gridmath.CPC_GRID["pixel_y"]
        self.assertIsNone(rasters.sample_point(self.cpc, x, y)[0])


class TestLonLatTo5070(unittest.TestCase):
    def test_ames_iowa_lands_in_the_corn_belt(self):
        x, y = rasters.lonlat_to_5070(-93.62, 42.03)
        self.assertTrue(-500000 < x < 500000)
        self.assertTrue(1800000 < y < 2400000)

    def test_result_is_finite(self):
        x, y = rasters.lonlat_to_5070(-96.0, 40.0)
        self.assertTrue(np.isfinite(x) and np.isfinite(y))


class TestEncodePng(unittest.TestCase):
    def test_emits_a_png_signature(self):
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        self.assertTrue(rasters.encode_png(rgba).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_ihdr_declares_the_expected_dimensions(self):
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        blob = rasters.encode_png(rgba)
        width, height = struct.unpack(">II", blob[16:24])
        self.assertEqual((width, height), (256, 256))

    def test_alpha_survives_the_round_trip(self):
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        rgba[..., 3] = 128
        blob = rasters.encode_png(rgba)
        name = "/vsimem/alpha_test.png"
        gdal.FileFromMemBuffer(name, blob)
        try:
            ds = gdal.Open(name)
            alpha = ds.GetRasterBand(4).ReadAsArray()
            self.assertTrue((alpha == 128).all())
        finally:
            gdal.Unlink(name)


class TestReadRat(unittest.TestCase):
    def test_maps_codes_to_class_names(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = str(fixtures.write_cdl_like(tmp / "cdl.tif"))
        classes = rasters.read_rat(path)
        self.assertEqual(classes[0], "Background")
        self.assertEqual(classes[1], "Corn")
        self.assertEqual(classes[5], "Soybeans")


if __name__ == "__main__":
    unittest.main()
