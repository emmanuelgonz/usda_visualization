import shutil
import tempfile
import unittest
from pathlib import Path

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


def _row(ur, start, cloud):
    sensor, tile = hls.parse_ur(ur)
    return {"id": ur, "tile": tile, "date": start[:10], "time": start, "sensor": sensor, "cloud": cloud}


SQUARE = [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 43.0], [-94.0, 42.0]]


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = hls.Store(self.tmp / "sub" / "hls.sqlite")
        self.addCleanup(self.store.close)
        self.rows = [
            _row("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10),
            _row("HLS.L30.T15TVH.2025202T160000.v2.0", "2025-07-21T16:00:00Z", 80),
            _row("HLS.S30.T15TVH.2025204T170000.v2.0", "2025-07-23T17:00:00Z", 10),
            _row("HLS.S30.T15TVH.2025206T170000.v2.0", "2025-07-25T17:00:00Z", None),
            _row("HLS.S30.T15TWH.2025199T170000.v2.0", "2025-07-18T17:01:00Z", 0),
        ]
        self.store.replace_month("S30", "2025-07", [r for r in self.rows if r["sensor"] == "S30"],
                                 "2025-08-02T00:00:00+00:00")
        self.store.replace_month("L30", "2025-07", [r for r in self.rows if r["sensor"] == "L30"],
                                 "2025-08-02T00:00:00+00:00")


class TestStoreWrites(StoreTestCase):
    def test_schema_creates_parent_directory_and_tables(self):
        names = {r[0] for r in self.store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"acq", "tiles", "months"})

    def test_schema_pins_the_covering_indexes(self):
        # counts() reads date, cloud and tile; acquisitions() reads tile, date,
        # cloud, sensor and time. Both must be answered from the index alone.
        names = {r[0] for r in self.store.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertEqual(names, {"acq_date", "acq_date_cloud_tile", "acq_tile_date_cover",
                                 "sqlite_autoindex_acq_1", "sqlite_autoindex_months_1",
                                 "sqlite_autoindex_tiles_1"})

    def test_reopening_drops_the_superseded_tile_date_index(self):
        self.store.conn.executescript("CREATE INDEX IF NOT EXISTS acq_tile_date ON acq(tile, date);")
        reopened = hls.Store(self.store.path)
        self.addCleanup(reopened.close)
        names = {r[0] for r in reopened.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertNotIn("acq_tile_date", names)
        self.assertIn("acq_tile_date_cover", names)

    def test_replace_month_is_idempotent(self):
        self.store.replace_month("S30", "2025-07", [r for r in self.rows if r["sensor"] == "S30"],
                                 "2025-08-03T00:00:00+00:00")
        n = self.store.conn.execute("SELECT COUNT(*) FROM acq").fetchone()[0]
        self.assertEqual(n, 5)
        self.assertEqual(self.store.fetched_at("S30", "2025-07"), "2025-08-03T00:00:00+00:00")
        self.assertIsNone(self.store.fetched_at("S30", "2025-06"))

    def test_replace_month_drops_rows_no_longer_present(self):
        self.store.replace_month("S30", "2025-07", [self.rows[0]], "2025-08-03T00:00:00+00:00")
        n = self.store.conn.execute("SELECT COUNT(*) FROM acq WHERE sensor='S30'").fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(self.store.conn.execute(
            "SELECT count FROM months WHERE sensor='S30' AND month='2025-07'").fetchone()[0], 1)

    def test_put_tiles_writes_many_in_one_call(self):
        self.store.put_tiles([("T15TVH", SQUARE), ("T15TWH", SQUARE)])
        self.assertEqual([f["properties"]["tile"] for f in self.store.tiles_geojson()["features"]],
                         ["T15TVH", "T15TWH"])

    def test_distinct_tiles_and_put_tile(self):
        self.assertEqual(self.store.distinct_tiles(), ["T15TVH", "T15TWH"])
        self.store.put_tile("T15TVH", SQUARE)
        self.assertEqual(self.store.distinct_tiles(), ["T15TVH", "T15TWH"])
        geo = self.store.tiles_geojson()
        self.assertEqual(geo["type"], "FeatureCollection")
        self.assertEqual(geo["features"][0]["properties"], {"tile": "T15TVH"})
        self.assertEqual(geo["features"][0]["geometry"]["coordinates"][0], SQUARE)

    def test_summary(self):
        self.store.put_tile("T15TVH", SQUARE)
        self.assertEqual(self.store.summary(),
                         {"count": 5, "fetched": "2025-08-02T00:00:00+00:00", "tiles": 1})


class TestStoreReads(StoreTestCase):
    def test_counts_apply_cloud_and_sensor(self):
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 30), {"T15TVH": 2, "T15TWH": 1})
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 30, "L30"), {})
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 80, "L30"), {"T15TVH": 1})
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 10, "S30"), {"T15TVH": 2, "T15TWH": 1})

    def test_counts_cloud_boundary_and_null(self):
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 9), {"T15TWH": 1})
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 100), {"T15TVH": 3, "T15TWH": 1})

    def test_counts_respect_the_date_range_inclusively(self):
        self.assertEqual(self.store.counts("2025-07-18", "2025-07-18", 30), {"T15TVH": 1, "T15TWH": 1})
        self.assertEqual(self.store.counts("2025-07-19", "2025-07-22", 100), {"T15TVH": 1})

    def test_acquisitions_are_ordered_by_time_and_carry_nulls(self):
        rows = self.store.acquisitions(["T15TVH"], "2025-07-01", "2025-07-31")
        self.assertEqual([r["date"] for r in rows], ["2025-07-18", "2025-07-21", "2025-07-23", "2025-07-25"])
        self.assertEqual(rows[0], {"tile": "T15TVH", "date": "2025-07-18", "time": "2025-07-18T17:00:00Z",
                                   "sensor": "S30", "cloud": 10})
        self.assertIsNone(rows[3]["cloud"])
        self.assertEqual(self.store.acquisitions([], "2025-07-01", "2025-07-31"), [])

    def test_read_only_store_sees_committed_rows(self):
        ro = hls.Store(self.store.path, read_only=True)
        self.addCleanup(ro.close)
        self.assertEqual(ro.counts("2025-07-01", "2025-07-31", 100)["T15TVH"], 3)
        with self.assertRaises(Exception):
            ro.put_tile("T15TVH", SQUARE)


class TestTileIndex(StoreTestCase):
    def test_covering_is_sorted_and_respects_rings(self):
        self.store.put_tile("T15TWH", [[-93.5, 42.0], [-92.5, 42.0], [-92.5, 43.0], [-93.5, 43.0], [-93.5, 42.0]])
        self.store.put_tile("T15TVH", SQUARE)
        index = hls.TileIndex(self.store)
        self.assertEqual(index.covering(-93.2, 42.5), ["T15TVH", "T15TWH"])   # the overlap strip
        self.assertEqual(index.covering(-93.9, 42.5), ["T15TVH"])
        self.assertEqual(index.covering(-90.0, 42.5), [])

    def test_store_for_closes_the_cached_entry_when_the_file_disappears(self):
        moved = self.store.path.with_name("moved.sqlite")
        entry = hls.store_for(self.store.path)
        self.store.path.rename(moved)
        try:
            self.assertIsNone(hls.store_for(self.store.path))
            with self.assertRaises(Exception):
                entry[0].conn.execute("SELECT 1")
        finally:
            moved.rename(self.store.path)
        self.assertIsNot(hls.store_for(self.store.path)[0], entry[0])

    def test_store_for_raises_busy_while_a_fetch_holds_the_write_lock(self):
        # Building the tile index reads the tiles table, so the reopen is what
        # hits the lock; the server catches hls.BUSY and degrades the route.
        import os, sqlite3, time
        hls.store_for(self.store.path)
        self.store.put_tile("T15TVH", SQUARE)
        os.utime(self.store.path, ns=(time.time_ns() + 10 ** 9, time.time_ns() + 10 ** 9))
        blocker = sqlite3.connect(str(self.store.path), timeout=0.1)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        try:
            with self.assertRaises(hls.BUSY):
                hls.store_for(self.store.path)
            self.assertLess(time.monotonic() - started, 2.0)   # half a second, not the default five
        finally:
            blocker.rollback()
        self.assertEqual(hls.store_for(self.store.path)[1].covering(-93.5, 42.5), ["T15TVH"])

    def test_store_for_returns_none_when_absent_and_reopens_on_change(self):
        self.assertIsNone(hls.store_for(self.tmp / "nope.sqlite"))
        first = hls.store_for(self.store.path)
        self.assertIsInstance(first[0], hls.Store)
        self.assertIsInstance(first[1], hls.TileIndex)
        self.assertIs(hls.store_for(self.store.path)[0], first[0])
        self.store.put_tile("T15TVH", SQUARE)
        import os, time
        os.utime(self.store.path, ns=(time.time_ns() + 10 ** 9, time.time_ns() + 10 ** 9))
        second = hls.store_for(self.store.path)
        self.assertIsNot(second[0], first[0])
        self.assertEqual(second[1].covering(-93.5, 42.5), ["T15TVH"])


class TestTileRing(unittest.TestCase):
    """Reference outlines are full-tile CMR footprints and the UTM grid itself."""

    def bbox(self, ring):
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return (min(xs), min(ys), max(xs), max(ys))

    def test_odd_zone_tile_matches_its_full_cmr_footprint(self):
        ring = hls.tile_ring("T15TVH")            # zone 15, band T; CMR full footprint below
        self.assertEqual(len(ring), 5)
        self.assertEqual(ring[0], ring[-1])
        for got, want in zip(self.bbox(ring), (-94.2292, 42.3576, -92.8796, 43.3528)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_another_odd_zone_band_u_tile(self):
        # CMR footprint of one T19UEQ granule: west -69.0003, south 48.6608, north 49.6527;
        # its east edge is a swath cut (slanted, -68.01 to -67.55), so it is not a reference.
        ring = hls.tile_ring("T19UEQ")
        west, south, _, north = self.bbox(ring)
        self.assertAlmostEqual(west, -69.0003, delta=0.01)
        self.assertAlmostEqual(south, 48.6608, delta=0.01)
        self.assertAlmostEqual(north, 49.6527, delta=0.01)

    def test_even_zone_row_offset_and_nw_anchoring_in_utm(self):
        # In an even zone the row letters are offset by five; T10TGR's square is
        # (externally confirmed: a CMR footprint of T10TGR spans northing
        # 4990.2-5100.0 km from easting 700.0 km, and one of T12TUT spans
        # 5190.2-5300.0 km to easting 409.8 km, zones 10 and 12)
        # easting 700-800 km, northing 5000-5100 km, and the 109.8 km tile hangs
        # from the square's north-west corner: 700-809.8 km by 4990.2-5100 km.
        ring = hls.tile_ring("T10TGR")
        _, to_utm = hls._transforms_for(10)
        utm = [to_utm.TransformPoint(p[0], p[1])[:2] for p in ring[:-1]]
        self.assertAlmostEqual(utm[0][0], 700000.0, delta=1.0)
        self.assertAlmostEqual(utm[0][1], 4990200.0, delta=1.0)
        self.assertAlmostEqual(utm[2][0], 809800.0, delta=1.0)
        self.assertAlmostEqual(utm[2][1], 5100000.0, delta=1.0)

    def test_column_letter_outside_the_zone_set_and_row_outside_the_band_are_none(self):
        self.assertIsNone(hls.tile_ring("T15TAH"))       # zone 15 uses columns S-Z
        self.assertIsNone(hls.tile_ring("T10TZR"))       # zone 10 uses columns A-H
        self.assertIsNone(hls.tile_ring("T15TVA"))       # row A lands 2,000 km north of band T

    def test_malformed_or_southern_ids_are_none(self):
        self.assertIsNone(hls.tile_ring("15TVH"))
        self.assertIsNone(hls.tile_ring("T15TVI"))       # I is not a row letter
        self.assertIsNone(hls.tile_ring("T61TVH"))
        self.assertIsNone(hls.tile_ring("T23KKQ"))       # southern hemisphere band
        self.assertIsNone(hls.tile_ring(None))


class TestClearHelpers(unittest.TestCase):
    ROWS = [
        {"tile": "T1", "date": "2025-07-18", "time": "2025-07-18T17:00:00Z", "sensor": "S30", "cloud": 10},
        {"tile": "T1", "date": "2025-07-21", "time": "2025-07-21T16:00:00Z", "sensor": "L30", "cloud": 80},
        {"tile": "T1", "date": "2025-07-22", "time": "2025-07-22T17:00:00Z", "sensor": "S30", "cloud": 5},
        {"tile": "T1", "date": "2025-07-22", "time": "2025-07-22T16:00:00Z", "sensor": "L30", "cloud": 5},
        {"tile": "T1", "date": "2025-07-25", "time": "2025-07-25T17:00:00Z", "sensor": "S30", "cloud": None},
    ]

    def test_is_clear(self):
        self.assertTrue(hls.is_clear(self.ROWS[0], 30, "ALL"))
        self.assertTrue(hls.is_clear(self.ROWS[0], 10, "S30"))
        self.assertFalse(hls.is_clear(self.ROWS[0], 9, "ALL"))
        self.assertFalse(hls.is_clear(self.ROWS[0], 30, "L30"))
        self.assertFalse(hls.is_clear(self.ROWS[4], 100, "ALL"))

    def test_nearest_clear_picks_smallest_abs_dt_then_earlier_time(self):
        clear = [r for r in self.ROWS if hls.is_clear(r, 30, "ALL")]
        self.assertEqual(hls.nearest_clear(clear, "2025-07-20", 7),
                         {"date": "2025-07-18", "sensor": "S30", "cloud": 10, "dt": -2})
        # 07-22 has two clear rows two days away; the 16:00 L30 row is earlier.
        self.assertEqual(hls.nearest_clear(clear, "2025-07-24", 7)["sensor"], "L30")
        self.assertEqual(hls.nearest_clear(clear, "2025-07-24", 7)["dt"], -2)

    def test_nearest_clear_respects_the_window(self):
        clear = [r for r in self.ROWS if hls.is_clear(r, 30, "ALL")]
        self.assertIsNone(hls.nearest_clear(clear, "2025-08-10", 7))
        self.assertIsNotNone(hls.nearest_clear(clear, "2025-07-29", 7))
        self.assertIsNone(hls.nearest_clear(clear, "2025-07-29", 6))
        self.assertIsNone(hls.nearest_clear([], "2025-07-20", 7))


if __name__ == "__main__":
    unittest.main()
