import re
import unittest

from viz import months


class TestMonths(unittest.TestCase):
    def test_between_bounds_and_frozen(self):
        self.assertEqual(months.months_between("2022-11", "2023-02"), ["2022-11", "2022-12", "2023-01", "2023-02"])
        self.assertEqual(months.month_bounds("2024-12"), ("2024-12-01", "2025-01-01"))
        self.assertFalse(months.is_frozen("2025-07", "2025-09-29T12:00:00+00:00"))
        self.assertTrue(months.is_frozen("2025-07", "2025-09-30T00:00:00Z"))

    def test_current_month_and_now_iso_shapes(self):
        self.assertRegex(months.current_month(), r"^\d{4}-\d{2}$")
        self.assertRegex(months.now_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
