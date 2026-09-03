import unittest

from viz import naming


class TestParseCpcFilename(unittest.TestCase):
    def test_parses_condition_filename(self):
        self.assertEqual(
            naming.parse_cpc_filename("cornCond24w15.tif"),
            {"crop": "corn", "var": "cond", "year": 2024, "week": 15},
        )

    def test_parses_progress_filename(self):
        self.assertEqual(
            naming.parse_cpc_filename("cottonProg16w42.tif"),
            {"crop": "cotton", "var": "prog", "year": 2016, "week": 42},
        )

    def test_parses_two_digit_week(self):
        self.assertEqual(naming.parse_cpc_filename("soyProg26w09.tif")["week"], 9)

    def test_rejects_non_matching_names(self):
        for bad in ("readme.txt", "cornCond24.tif", "corn_Cond24w15.tif", "cornCond24w15.tfw"):
            with self.subTest(bad=bad):
                self.assertIsNone(naming.parse_cpc_filename(bad))


class TestCpcRelpath(unittest.TestCase):
    def test_round_trips_with_parser(self):
        rel = naming.cpc_relpath("wheat", "cond", 2019, 7)
        self.assertEqual(rel, "wheat/cond/wheatCond19w07.tif")
        self.assertEqual(
            naming.parse_cpc_filename(rel.split("/")[-1]),
            {"crop": "wheat", "var": "cond", "year": 2019, "week": 7},
        )


class TestCropCodes(unittest.TestCase):
    def test_every_crop_has_both_sets(self):
        self.assertEqual(set(naming.CROP_CODES), set(naming.CROPS))
        for crop, sets in naming.CROP_CODES.items():
            with self.subTest(crop=crop):
                self.assertEqual(set(sets), {"primary", "double"})

    def test_primary_codes_match_spec(self):
        self.assertEqual(naming.CROP_CODES["corn"]["primary"], (1,))
        self.assertEqual(naming.CROP_CODES["cotton"]["primary"], (2,))
        self.assertEqual(naming.CROP_CODES["soy"]["primary"], (5,))
        self.assertEqual(naming.CROP_CODES["wheat"]["primary"], (22, 23, 24))

    def test_double_crop_codes_count_toward_both_constituents(self):
        # 26 = Dbl Crop WinWht/Soybeans
        self.assertIn(26, naming.CROP_CODES["soy"]["double"])
        self.assertIn(26, naming.CROP_CODES["wheat"]["double"])
        # 225 = Dbl Crop WinWht/Corn
        self.assertIn(225, naming.CROP_CODES["corn"]["double"])
        self.assertIn(225, naming.CROP_CODES["wheat"]["double"])
        # 241 = Dbl Crop Corn/Soybeans
        self.assertIn(241, naming.CROP_CODES["corn"]["double"])
        self.assertIn(241, naming.CROP_CODES["soy"]["double"])
        # 239 = Dbl Crop Soybeans/Cotton
        self.assertIn(239, naming.CROP_CODES["soy"]["double"])
        self.assertIn(239, naming.CROP_CODES["cotton"]["double"])

    def test_excluded_classes_appear_nowhere(self):
        excluded = {12, 13, 39}  # Sweet Corn, Pop or Orn Corn, Buckwheat
        for crop, sets in naming.CROP_CODES.items():
            for kind, codes in sets.items():
                with self.subTest(crop=crop, kind=kind):
                    self.assertEqual(excluded & set(codes), set())

    def test_primary_and_double_never_overlap_within_a_crop(self):
        for crop, sets in naming.CROP_CODES.items():
            with self.subTest(crop=crop):
                self.assertEqual(set(sets["primary"]) & set(sets["double"]), set())

    def test_all_codes_are_valid_byte_values(self):
        for sets in naming.CROP_CODES.values():
            for codes in sets.values():
                for code in codes:
                    self.assertTrue(0 < code < 256)


class TestPairCdlYear(unittest.TestCase):
    CDL_YEARS = (2022, 2023, 2024, 2025)

    def test_exact_match_when_available(self):
        for year in self.CDL_YEARS:
            with self.subTest(year=year):
                self.assertEqual(naming.pair_cdl_year(year, self.CDL_YEARS), year)

    def test_years_before_coverage_pair_with_earliest(self):
        for year in range(2015, 2022):
            with self.subTest(year=year):
                self.assertEqual(naming.pair_cdl_year(year, self.CDL_YEARS), 2022)

    def test_years_after_coverage_pair_with_latest(self):
        self.assertEqual(naming.pair_cdl_year(2026, self.CDL_YEARS), 2025)

    def test_raises_when_no_cdl_years(self):
        with self.assertRaises(ValueError):
            naming.pair_cdl_year(2024, ())


if __name__ == "__main__":
    unittest.main()
