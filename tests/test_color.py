import unittest

import numpy as np

from viz import color


class TestRampLut(unittest.TestCase):
    def test_shape_and_dtype(self):
        for var in ("cond", "prog"):
            with self.subTest(var=var):
                lut = color.ramp_lut(var)
                self.assertEqual(lut.shape, (256, 4))
                self.assertEqual(lut.dtype, np.uint8)

    def test_fully_opaque_across_the_ramp(self):
        for var in ("cond", "prog"):
            with self.subTest(var=var):
                self.assertTrue((color.ramp_lut(var)[:, 3] == 255).all())

    def test_condition_ramp_runs_red_to_green(self):
        lut = color.ramp_lut("cond")
        low, high = lut[0], lut[255]
        self.assertGreater(int(low[0]), int(low[1]))    # low end is red-dominant
        self.assertGreater(int(high[1]), int(high[0]))  # high end is green-dominant

    def test_ramp_is_monotonic_in_perceived_lightness_free_hue(self):
        # Adjacent entries must not jump: a ramp with a discontinuity reads as banding.
        for var in ("cond", "prog"):
            lut = color.ramp_lut(var).astype(int)
            deltas = np.abs(np.diff(lut[:, :3], axis=0)).max()
            with self.subTest(var=var):
                self.assertLessEqual(deltas, 12)

    def test_unknown_variable_raises(self):
        with self.assertRaises(KeyError):
            color.ramp_lut("nope")


class TestColorizeContinuous(unittest.TestCase):
    def test_domain_endpoints_hit_ramp_endpoints(self):
        values = np.array([[1.0, 5.0]], dtype=np.float32)
        out = color.colorize_continuous(values, "cond")
        lut = color.ramp_lut("cond")
        np.testing.assert_array_equal(out[0, 0, :3], lut[0, :3])
        np.testing.assert_array_equal(out[0, 1, :3], lut[255, :3])

    def test_values_outside_the_domain_clamp(self):
        values = np.array([[0.2, 9.9]], dtype=np.float32)
        out = color.colorize_continuous(values, "cond")
        lut = color.ramp_lut("cond")
        np.testing.assert_array_equal(out[0, 0, :3], lut[0, :3])
        np.testing.assert_array_equal(out[0, 1, :3], lut[255, :3])

    def test_nan_and_nodata_are_transparent(self):
        values = np.array([[np.nan, -9999.0, 3.0]], dtype=np.float32)
        out = color.colorize_continuous(values, "cond")
        self.assertEqual(int(out[0, 0, 3]), 0)
        self.assertEqual(int(out[0, 1, 3]), 0)
        self.assertEqual(int(out[0, 2, 3]), 255)

    def test_alpha_argument_scales_opacity_and_still_zeroes_nodata(self):
        values = np.array([[3.0, 3.0, np.nan]], dtype=np.float32)
        alpha = np.array([[0.0, 0.5, 1.0]], dtype=np.float32)
        out = color.colorize_continuous(values, "cond", alpha=alpha)
        self.assertEqual(int(out[0, 0, 3]), 0)
        self.assertEqual(int(out[0, 1, 3]), 128)  # 255 * 0.5 = 127.5, NumPy rounds half to even
        self.assertEqual(int(out[0, 2, 3]), 0)

    def test_progress_domain_is_zero_to_one(self):
        values = np.array([[0.0, 1.0]], dtype=np.float32)
        out = color.colorize_continuous(values, "prog")
        lut = color.ramp_lut("prog")
        np.testing.assert_array_equal(out[0, 0, :3], lut[0, :3])
        np.testing.assert_array_equal(out[0, 1, :3], lut[255, :3])

    def test_output_shape_matches_input(self):
        values = np.full((7, 11), 3.0, dtype=np.float32)
        self.assertEqual(color.colorize_continuous(values, "cond").shape, (7, 11, 4))


class _FakeColorTable:
    def __init__(self, entries):
        self._entries = entries

    def GetColorEntry(self, index):
        return self._entries.get(index)


class TestPaletteLut(unittest.TestCase):
    def test_background_class_zero_is_transparent(self):
        table = _FakeColorTable({0: (0, 0, 0, 255), 1: (255, 210, 0, 255)})
        lut = color.palette_lut(table)
        self.assertEqual(tuple(lut[0]), (0, 0, 0, 0))

    def test_known_classes_keep_their_colors(self):
        table = _FakeColorTable({1: (255, 210, 0, 255), 5: (36, 110, 0, 255)})
        lut = color.palette_lut(table)
        self.assertEqual(tuple(lut[1]), (255, 210, 0, 255))
        self.assertEqual(tuple(lut[5]), (36, 110, 0, 255))

    def test_missing_entries_are_transparent(self):
        lut = color.palette_lut(_FakeColorTable({1: (255, 210, 0, 255)}))
        self.assertEqual(tuple(lut[200]), (0, 0, 0, 0))

    def test_none_color_table_yields_a_fully_transparent_lut(self):
        lut = color.palette_lut(None)
        self.assertEqual(lut.shape, (256, 4))
        self.assertTrue((lut[:, 3] == 0).all())


class TestFocusLut(unittest.TestCase):
    def _base(self):
        return color.palette_lut(_FakeColorTable({
            0: (0, 0, 0, 255), 1: (255, 210, 0, 255), 5: (36, 110, 0, 255),
            111: (72, 112, 163, 255), 225: (255, 210, 0, 255),
        }))

    def test_kept_codes_are_byte_identical(self):
        base = self._base()
        out = color.focus_lut(base, (1, 225))
        np.testing.assert_array_equal(out[1], base[1])
        np.testing.assert_array_equal(out[225], base[225])

    def test_other_codes_become_luminance_grey_with_alpha_kept(self):
        out = color.focus_lut(self._base(), (1,))
        r, g, b, a = (int(v) for v in out[5])
        self.assertEqual(r, g); self.assertEqual(g, b); self.assertEqual(a, 255)
        # Rec. 601 luminance of (36, 110, 0) is about 75.
        self.assertTrue(70 <= r <= 80, r)
        water = out[111]
        self.assertTrue(int(water[0]) < int(out[5][0]) + 60)  # still darker than a light class would be

    def test_class_zero_stays_transparent(self):
        self.assertEqual(int(color.focus_lut(self._base(), (1,))[0, 3]), 0)

    def test_does_not_mutate_the_input(self):
        base = self._base(); before = base.copy()
        color.focus_lut(base, (1,))
        np.testing.assert_array_equal(base, before)


class TestColorizeThematic(unittest.TestCase):
    def test_maps_codes_through_the_lut(self):
        lut = color.palette_lut(_FakeColorTable({1: (255, 210, 0, 255)}))
        codes = np.array([[1, 0]], dtype=np.uint8)
        out = color.colorize_thematic(codes, lut)
        self.assertEqual(tuple(out[0, 0]), (255, 210, 0, 255))
        self.assertEqual(int(out[0, 1, 3]), 0)


class TestLegendStops(unittest.TestCase):
    def test_condition_stops_name_the_nass_categories(self):
        labels = [stop["label"] for stop in color.legend_stops("cond")]
        self.assertEqual(labels, ["Very poor", "Poor", "Fair", "Good", "Excellent"])

    def test_every_stop_carries_a_value_and_a_css_color(self):
        for var in ("cond", "prog"):
            for stop in color.legend_stops(var):
                with self.subTest(var=var, stop=stop):
                    self.assertIn("value", stop)
                    self.assertTrue(stop["color"].startswith("#"))
                    self.assertEqual(len(stop["color"]), 7)


if __name__ == "__main__":
    unittest.main()
