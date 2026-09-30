import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from viz import catalog

RING = [[-100, 40], [-99, 40], [-99, 41], [-100, 41], [-100, 40]]


def swath_row(gid, start, **extra):
    row = {"id": gid, "start": start, "end": start, "minlon": -100, "minlat": 40, "maxlon": -99, "maxlat": 41,
           "ring": RING, "browse": f"https://x/{gid}.png"}
    row.update(extra)
    return row


def tiled_row(gid, start, tile, cloud, sensor):
    return {"id": gid, "start": start, "end": start, "tile": tile, "cloud": cloud, "sensor": sensor}


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "cat" / "test.sqlite"
        self.cat = catalog.Catalog(self.path)

    def tearDown(self):
        self.cat.close()
        shutil.rmtree(self.tmp)

    def test_creates_schema_and_parent_directory(self):
        names = {r[0] for r in self.cat.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"granules", "tiles", "coverage", "months"})
        indexes = {r[0] for r in self.cat.conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'granules_%'")}
        self.assertEqual(indexes, {"granules_mission_start", "granules_mission_tile", "granules_mission_lat"})

    def test_bulk_mode_sets_pragmas(self):
        self.cat.bulk_mode()
        self.assertEqual(self.cat.conn.execute("PRAGMA synchronous").fetchone()[0], 0)
        self.assertEqual(self.cat.conn.execute("PRAGMA journal_mode").fetchone()[0], "memory")
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z")], "2025-08-01T00:00:00+00:00")
        self.assertEqual(self.cat.conn.execute("SELECT COUNT(*) FROM granules").fetchone()[0], 1)

    def test_replace_month_twice_gives_the_same_rows_and_records_the_fetch(self):
        rows = [swath_row("a", "2025-07-02T10:00:00Z", cloud=5.0), swath_row("b", "2025-07-30T10:00:00Z")]
        self.cat.replace_month("sw", "2025-07", rows, "2025-08-01T00:00:00+00:00")
        self.cat.replace_month("sw", "2025-07", rows, "2025-08-02T00:00:00+00:00")
        got = self.cat.conn.execute("SELECT id, cloud, ring, attrs FROM granules WHERE mission='sw' ORDER BY id").fetchall()
        self.assertEqual([(r["id"], r["cloud"]) for r in got], [("a", 5.0), ("b", None)])
        self.assertEqual(got[0]["ring"], '[[-100, 40], [-99, 40], [-99, 41], [-100, 41], [-100, 40]]')
        self.assertIsNone(got[0]["attrs"])
        self.assertEqual(self.cat.fetched_at("sw", "2025-07"), "2025-08-02T00:00:00+00:00")
        self.assertIsNone(self.cat.fetched_at("sw", "2025-08"))
        self.assertEqual(self.cat.summary("sw"), {"count": 2, "fetched": "2025-08-02T00:00:00+00:00", "months": 1})

    def test_replace_month_only_touches_that_mission_and_month(self):
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z")], "x")
        self.cat.replace_month("sw", "2025-08", [swath_row("c", "2025-08-02T10:00:00Z")], "x")
        self.cat.replace_month("ti", "2025-07", [tiled_row("t1", "2025-07-05T00:00:00Z", "T15TVH", 10, "A")], "x")
        self.cat.replace_month("sw", "2025-07", [], "y")
        ids = [r[0] for r in self.cat.conn.execute("SELECT id FROM granules ORDER BY id")]
        self.assertEqual(ids, ["c", "t1"])
        self.assertEqual(self.cat.missions_present(), ["sw", "ti"])

    def test_extra_attributes_go_to_attrs_json(self):
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z", attrs={"swir": 1})], "x")
        self.assertEqual(self.cat.conn.execute("SELECT attrs FROM granules").fetchone()[0], '{"swir": 1}')

    def test_undeclared_keys_and_explicit_attrs_merge_into_attrs(self):
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z", swir=3, attrs={"x": 1})], "x")
        stored = self.cat.conn.execute("SELECT attrs FROM granules").fetchone()[0]
        self.assertEqual(json.loads(stored), {"x": 1, "swir": 3})

    def test_replace_month_removes_coverage_of_vanished_granules(self):
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z"), swath_row("b", "2025-07-03T10:00:00Z")], "x")
        self.cat.put_coverage([("sw", "a", "mgrs", "T1"), ("sw", "b", "mgrs", "T1")])
        self.cat.replace_month("sw", "2025-07", [swath_row("b", "2025-07-03T10:00:00Z")], "y")
        self.assertEqual([r[0] for r in self.cat.conn.execute("SELECT id FROM coverage")], [])

    def test_tiles_and_coverage(self):
        self.cat.replace_month("ti", "2025-07", [tiled_row("t1", "2025-07-05T00:00:00Z", "T15TVH", 10, "A"),
                                                  tiled_row("t2", "2025-07-06T00:00:00Z", "T15TVG", 10, "A")], "x")
        self.assertEqual(self.cat.distinct_tiles("ti"), ["T15TVG", "T15TVH"])
        self.assertEqual(self.cat.known_tiles("mgrs"), set())
        self.cat.put_tiles("mgrs", [("T15TVH", RING)])
        self.assertEqual(self.cat.known_tiles("mgrs"), {"T15TVH"})
        self.assertEqual(self.cat.tile_rings("mgrs"), [("T15TVH", RING)])
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z")], "x")
        self.assertEqual([u[0] for u in self.cat.uncovered("sw", "mgrs")], ["a"])
        self.assertEqual(self.cat.uncovered("sw", "mgrs")[0][1], (-100.0, 40.0, -99.0, 41.0))
        self.cat.put_coverage([("sw", "a", "mgrs", "T15TVH")])
        self.cat.put_coverage([("sw", "a", "mgrs", "T15TVH")])          # idempotent
        self.assertEqual(self.cat.uncovered("sw", "mgrs"), [])
        self.assertEqual(self.cat.conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0], 1)

    def test_read_only_open_and_busy(self):
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z")], "x")
        ro = catalog.open_read_only(self.path)
        try:
            self.assertEqual(ro.summary("sw")["count"], 1)
            with self.assertRaises(sqlite3.OperationalError):
                ro.conn.execute("INSERT INTO months VALUES ('z', '2025-01', 0, 'x')")
        finally:
            ro.close()
        with self.assertRaises(FileNotFoundError):
            catalog.open_read_only(self.tmp / "missing.sqlite")
        # a writer holding the lock makes a read-only read raise BUSY within the short timeout
        writer = sqlite3.connect(str(self.path), isolation_level=None)
        writer.execute("BEGIN EXCLUSIVE")
        try:
            ro = catalog.open_read_only(self.path)
            try:
                with self.assertRaises(catalog.BUSY):
                    ro.summary("sw")
            finally:
                ro.close()
        finally:
            writer.execute("ROLLBACK")
            writer.close()
