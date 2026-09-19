import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal

from tests import fixtures
from viz import naming, prepare


class TestScanCpc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for rel in (
            "corn/cond/cornCond24w15.tif",
            "corn/cond/cornCond24w16.tif",
            "corn/cond/cornCond23w20.tif",
            "corn/prog/cornProg24w15.tif",
            "soy/cond/soyCond24w22.tif",
            "corn/cond/notes.txt",
        ):
            target = self.tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")

    def test_groups_weeks_by_crop_variable_and_year(self):
        found = prepare.scan_cpc(self.tmp)
        self.assertEqual(found["corn"]["cond"]["2024"], [15, 16])
        self.assertEqual(found["corn"]["cond"]["2023"], [20])
        self.assertEqual(found["corn"]["prog"]["2024"], [15])
        self.assertEqual(found["soy"]["cond"]["2024"], [22])

    def test_ignores_non_raster_files(self):
        found = prepare.scan_cpc(self.tmp)
        self.assertNotIn("notes", str(found))

    def test_weeks_are_sorted_ascending(self):
        (self.tmp / "corn/cond/cornCond24w09.tif").write_bytes(b"x")
        self.assertEqual(prepare.scan_cpc(self.tmp)["corn"]["cond"]["2024"], [9, 15, 16])


class TestScanCdl(unittest.TestCase):
    def test_reports_years_present_sorted(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name in ("2024_30m_cdls.tif", "2022_30m_cdls.tif", "2022_30m_cdls.tif.ovr"):
            (tmp / name).write_bytes(b"x")
        self.assertEqual(prepare.scan_cdl(tmp), [2022, 2024])


class TestBuildLutVrt(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = str(fixtures.write_cdl_like(self.tmp / "cdl.tif", code=1))

    def test_produces_one_band_per_code_set(self):
        xml = prepare.build_lut_vrt(self.src, [(1,), (5,), (22, 23, 24)])
        ds = gdal.Open(xml)
        self.assertEqual(ds.RasterCount, 3)

    def test_bands_are_gray_not_paletted(self):
        ds = gdal.Open(prepare.build_lut_vrt(self.src, [(1,)]))
        interp = ds.GetRasterBand(1).GetColorInterpretation()
        self.assertEqual(gdal.GetColorInterpretationName(interp), "Gray")

    def test_lut_maps_member_codes_to_one_and_others_to_zero(self):
        ds = gdal.Open(prepare.build_lut_vrt(self.src, [(1,)]))
        # Held in a variable rather than chained: GDAL 3.8.4's Band object keeps
        # only a weakref to its parent Dataset, so an unnamed gdal.Open(...)
        # temporary can be collected before ReadAsArray() runs on its band.
        src_ds = gdal.Open(self.src)
        raw = src_ds.GetRasterBand(1).ReadAsArray()
        lutted = ds.GetRasterBand(1).ReadAsArray()
        np.testing.assert_array_equal(lutted, (raw == 1).astype(np.uint8))

    def test_empty_code_set_yields_an_all_zero_band(self):
        ds = gdal.Open(prepare.build_lut_vrt(self.src, [()]))
        self.assertTrue((ds.GetRasterBand(1).ReadAsArray() == 0).all())


class TestBuildMask(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # 600 x 600 at the fixture's 300 m pixel spans 180 x 180 km, about 20 x 20
        # cells of the 9 km CPC grid, with the right half corn. Large enough that
        # interior cells reach a fraction near 1.0 rather than only edge effects.
        self.src = str(fixtures.write_cdl_like(self.tmp / "cdl.tif", width=600, height=600, code=1))

    def test_writes_two_bands(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        self.assertEqual(gdal.Open(str(out)).RasterCount, 2)

    def test_fractions_stay_within_zero_and_one(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        data = gdal.Open(str(out)).ReadAsArray()
        finite = data[np.isfinite(data)]
        self.assertGreaterEqual(float(finite.min()), 0.0)
        self.assertLessEqual(float(finite.max()), 1.0)

    def test_band_sum_never_exceeds_one(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        data = np.nan_to_num(gdal.Open(str(out)).ReadAsArray())
        self.assertLessEqual(float((data[0] + data[1]).max()), 1.0 + 1e-6)

    def test_primary_band_reflects_actual_corn_cover(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        data = gdal.Open(str(out)).ReadAsArray()
        # The fixture is corn on its right half, so the covered cells average near 0.5.
        covered = data[0][data[0] > 0]
        self.assertGreater(float(covered.max()), 0.2)

    def test_double_crop_band_is_zero_when_no_double_crop_codes_present(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        data = np.nan_to_num(gdal.Open(str(out)).ReadAsArray())
        self.assertAlmostEqual(float(data[1].max()), 0.0, places=6)

    def test_output_is_on_the_canonical_cpc_grid(self):
        from viz import gridmath
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        ds = gdal.Open(str(out))
        self.assertEqual((ds.RasterXSize, ds.RasterYSize),
                         (gridmath.CPC_GRID["width"], gridmath.CPC_GRID["height"]))
        gt = ds.GetGeoTransform()
        self.assertAlmostEqual(gt[1], gridmath.CPC_GRID["pixel_x"], places=6)
        self.assertAlmostEqual(gt[5], gridmath.CPC_GRID["pixel_y"], places=6)


class TestScanMasks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_reports_only_years_with_a_complete_crop_set(self):
        for crop in naming.CROPS:
            (self.tmp / f"2024_{crop}_frac9km.tif").write_bytes(b"x")
        (self.tmp / "2025_corn_frac9km.tif").write_bytes(b"x")  # incomplete
        self.assertEqual(prepare.scan_masks(self.tmp), [2024])

    def test_empty_directory_yields_no_years(self):
        self.assertEqual(prepare.scan_masks(self.tmp), [])


class TestBuildCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cpc = self.tmp / "cpc"
        for rel in ("corn/cond/cornCond24w30.tif", "corn/prog/cornProg18w30.tif"):
            target = self.cpc / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
        self.cdl = self.tmp / "cdl"
        self.cdl.mkdir()
        fixtures.write_cdl_like(self.cdl / "2024_30m_cdls.tif")
        self.masks = self.tmp / "masks"
        self.masks.mkdir()

    def test_records_crops_variables_and_cdl_years(self):
        catalog = prepare.build_catalog(self.cpc, self.cdl, self.masks)
        self.assertEqual(tuple(catalog["crops"]), naming.CROPS)
        self.assertEqual(tuple(catalog["vars"]), naming.VARS)
        self.assertEqual(catalog["cdl_years"], [2024])

    def test_records_crop_codes_and_class_names(self):
        catalog = prepare.build_catalog(self.cpc, self.cdl, self.masks)
        self.assertEqual(catalog["crop_codes"]["corn"]["primary"], [1])
        self.assertEqual(catalog["cdl_classes"]["1"], "Corn")

    def test_pairs_every_cpc_year_with_a_cdl_year(self):
        catalog = prepare.build_catalog(self.cpc, self.cdl, self.masks)
        self.assertEqual(catalog["cdl_pairing"]["2018"], 2024)
        self.assertEqual(catalog["cdl_pairing"]["2024"], 2024)

    def test_mask_years_is_empty_when_no_masks_are_built(self):
        self.assertEqual(prepare.build_catalog(self.cpc, self.cdl, self.masks)["mask_years"], [])

    def test_mask_years_lists_years_with_a_complete_crop_set(self):
        for crop in naming.CROPS:
            (self.masks / f"2024_{crop}_frac9km.tif").write_bytes(b"x")
        self.assertEqual(
            prepare.build_catalog(self.cpc, self.cdl, self.masks)["mask_years"], [2024]
        )

    def test_serializes_to_json(self):
        catalog = prepare.build_catalog(self.cpc, self.cdl, self.masks)
        self.assertIsInstance(json.dumps(catalog), str)


if __name__ == "__main__":
    unittest.main()
