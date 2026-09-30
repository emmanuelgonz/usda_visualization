import re
import unittest

from viz import hls, months


class TestMonths(unittest.TestCase):
    def test_between_bounds_and_frozen(self):
        self.assertEqual(months.months_between("2022-11", "2023-02"), ["2022-11", "2022-12", "2023-01", "2023-02"])
        self.assertEqual(months.month_bounds("2024-12"), ("2024-12-01", "2025-01-01"))
        self.assertFalse(months.is_frozen("2025-07", "2025-09-29T12:00:00+00:00"))
        self.assertTrue(months.is_frozen("2025-07", "2025-09-30T00:00:00Z"))

    def test_hls_re_exports_the_same_functions(self):
        self.assertIs(hls.months_between, months.months_between)
        self.assertIs(hls.month_bounds, months.month_bounds)
        self.assertIs(hls.is_frozen, months.is_frozen)

    def test_current_month_and_now_iso_shapes(self):
        self.assertRegex(months.current_month(), r"^\d{4}-\d{2}$")
        self.assertRegex(months.now_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
