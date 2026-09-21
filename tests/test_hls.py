import unittest

from viz import hls

CSV = (
    "Granule UR,Producer Granule ID,Start Time,End Time,Online Access URLs,Browse URLs,Cloud Cover,Day/Night,Size\n"
    "HLS.S30.T19UEQ.2025191T154001.v2.0,HLS.S30.T19UEQ.2025191T154001,2025-07-10T15:49:45.668Z,"
    "2025-07-10T15:49:45.668Z,\"https://a/x.tif,https://a/y.tif\",https://a/x.jpg,14,DAY,1.2\n"
    "HLS.L30.T18STE.2022001T154118.v2.0,HLS.L30.T18STE.2022001T154118,2022-01-01T15:41:18.815Z,"
    "2022-01-01T15:41:18.815Z,https://a/z.tif,,,DAY,1.1\n"
    "HLS.S30.T15TVH.2025203T170849.v2.0,HLS.S30.T15TVH.2025203T170849,2025-07-22T17:08:49.000Z,"
    "2025-07-22T17:08:49.000Z,https://a/w.tif,,100,DAY,1.0\n"
)


class TestParseUr(unittest.TestCase):
    def test_sensor_and_tile(self):
        self.assertEqual(hls.parse_ur("HLS.S30.T15TVH.2025203T170849.v2.0"), ("S30", "T15TVH"))
        self.assertEqual(hls.parse_ur("HLS.L30.T01ABC.2022001T154118.v2.0"), ("L30", "T01ABC"))

    def test_malformed_is_none(self):
        self.assertIsNone(hls.parse_ur("ECOv002_L2_LSTE_39898"))
        self.assertIsNone(hls.parse_ur(""))
        self.assertIsNone(hls.parse_ur(None))


class TestMonths(unittest.TestCase):
    def test_months_between_inclusive_across_a_year_end(self):
        self.assertEqual(hls.months_between("2022-11", "2023-02"),
                         ["2022-11", "2022-12", "2023-01", "2023-02"])

    def test_single_month(self):
        self.assertEqual(hls.months_between("2025-07", "2025-07"), ["2025-07"])

    def test_month_bounds(self):
        self.assertEqual(hls.month_bounds("2025-07"), ("2025-07-01", "2025-08-01"))
        self.assertEqual(hls.month_bounds("2024-12"), ("2024-12-01", "2025-01-01"))

    def test_frozen_at_sixty_days_after_month_end(self):
        # 2025-07 ends at 2025-08-01; 59 days later is 2025-09-29, 60 is 09-30, 61 is 10-01.
        self.assertFalse(hls.is_frozen("2025-07", "2025-09-29T12:00:00+00:00"))
        self.assertTrue(hls.is_frozen("2025-07", "2025-09-30T00:00:00+00:00"))
        self.assertTrue(hls.is_frozen("2025-07", "2025-10-01T00:00:00+00:00"))

    def test_frozen_accepts_z_suffix(self):
        self.assertTrue(hls.is_frozen("2025-07", "2025-12-01T00:00:00Z"))


class TestParseCsv(unittest.TestCase):
    def test_rows(self):
        rows = hls.parse_csv(CSV)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {
            "id": "HLS.S30.T19UEQ.2025191T154001.v2.0", "tile": "T19UEQ", "date": "2025-07-10",
            "time": "2025-07-10T15:49:45.668Z", "sensor": "S30", "cloud": 14,
        })
        self.assertEqual(rows[1]["sensor"], "L30")
        self.assertIsNone(rows[1]["cloud"])          # blank cloud cover
        self.assertEqual(rows[2]["cloud"], 100)

    def test_skips_rows_without_a_parseable_ur_or_start(self):
        text = CSV.splitlines()[0] + "\nNOT.AN.HLS.UR,x,2025-07-10T00:00:00Z,,,,5,DAY,1\n" \
               "HLS.S30.T19UEQ.2025191T154001.v2.0,x,,,,,5,DAY,1\n"
        self.assertEqual(hls.parse_csv(text), [])

    def test_decimal_cloud_is_truncated_to_int(self):
        text = CSV.splitlines()[0] + "\nHLS.S30.T19UEQ.2025191T154001.v2.0,x,2025-07-10T00:00:00Z,,,,14.6,DAY,1\n"
        self.assertEqual(hls.parse_csv(text)[0]["cloud"], 14)


if __name__ == "__main__":
    unittest.main()
