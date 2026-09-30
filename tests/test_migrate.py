import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from viz import catalog, grids, migrate, paths, registry

NOW = lambda: "2025-09-30T00:00:00+00:00"
RING_IA = [[-94, 41.5], [-93, 41.5], [-93, 42.5], [-94, 42.5], [-94, 41.5]]


OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS acq (id TEXT PRIMARY KEY, tile TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL, sensor TEXT NOT NULL, cloud INTEGER);
CREATE TABLE IF NOT EXISTS tiles (tile TEXT PRIMARY KEY, ring TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS months (sensor TEXT NOT NULL, month TEXT NOT NULL, count INTEGER NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY (sensor, month));
"""


class OldStore:
    """The retired HLS store's on-disk schema, written directly so the migrate tests can build a fixture."""

    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(OLD_SCHEMA)

    def replace_month(self, sensor, month, rows, fetched_at):
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO acq (id, tile, date, time, sensor, cloud) VALUES (?, ?, ?, ?, ?, ?)",
                                  [(r["id"], r["tile"], r["date"], r["time"], r["sensor"], r["cloud"]) for r in rows])
            self.conn.execute("INSERT OR REPLACE INTO months (sensor, month, count, fetched_at) VALUES (?, ?, ?, ?)",
                              (sensor, month, len(rows), fetched_at))

    def put_tiles(self, pairs):
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO tiles (tile, ring) VALUES (?, ?)",
                                  [(tile, json.dumps(ring)) for tile, ring in pairs])

    def close(self):
        self.conn.close()


def write_geojson(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


class TestMigrate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cat = catalog.Catalog(self.tmp / "c.sqlite")
        self.out = io.StringIO()
        store = OldStore(self.tmp / "hls.sqlite")
        store.replace_month("S30", "2025-07", [
            {"id": "HLS.S30.T15TVH.2025199T170000.v2.0", "tile": "T15TVH", "date": "2025-07-18", "time": "2025-07-18T17:00:00Z", "sensor": "S30", "cloud": 10},
            {"id": "HLS.S30.T15TVH.2025200T170000.v2.0", "tile": "T15TVH", "date": "2025-07-19", "time": "2025-07-19T17:00:00Z", "sensor": "S30", "cloud": None},
        ], "2025-08-01T00:00:00+00:00")
        store.replace_month("L30", "2025-07", [
            {"id": "HLS.L30.T15TVH.2025198T170000.v2.0", "tile": "T15TVH", "date": "2025-07-17", "time": "2025-07-17T17:00:00Z", "sensor": "L30", "cloud": 50},
        ], "2025-08-02T00:00:00+00:00")
        store.put_tiles([("T15TVH", grids.mgrs.ring("T15TVH"))])
        store.close()
        write_geojson(self.tmp / "emit.geojson", [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [RING_IA]},
            "properties": {"id": "EMIT_1", "start": "2025-07-18T16:00:00.000Z", "end": "2025-07-18T16:00:12.000Z", "cloud": 12.0,
                           "year": 2025, "browse": "https://x/EMIT_1.png", "data": "https://x/EMIT_1.nc",
                           "eco": [{"id": "ECO_1", "dt": 180}], "hls_near": [{"date": "2025-07-18"}]}}])
        write_geojson(self.tmp / "eco.geojson", [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[-95, 41], [-92, 41], [-92, 43], [-95, 43], [-95, 41]]]},
            "properties": {"id": "ECO_1", "start": "2025-07-18T16:03:00.000Z", "end": "2025-07-18T16:03:52.000Z",
                           "daynight": "DAY", "year": 2025, "orbit": 39607}}])
        self.reg = registry.load()

    def tearDown(self):
        self.cat.close()
        shutil.rmtree(self.tmp)

    def test_import_hls_collapses_sensor_months_and_copies_tiles(self):
        counts = migrate.import_hls(self.cat, self.tmp / "hls.sqlite", now=NOW, out=self.out)
        self.assertEqual(counts, {"granules": 3, "months": 1, "tiles": 1})
        rows = self.cat.conn.execute("SELECT id, tile, sensor, cloud, start FROM granules WHERE mission='hls' ORDER BY start").fetchall()
        self.assertEqual([(r["sensor"], r["cloud"]) for r in rows], [("L30", 50.0), ("S30", 10.0), ("S30", None)])
        self.assertEqual(rows[0]["start"], "2025-07-17T17:00:00Z")
        month = self.cat.conn.execute("SELECT count, fetched_at FROM months WHERE mission='hls'").fetchone()
        self.assertEqual((month["count"], month["fetched_at"]), (3, "2025-08-01T00:00:00+00:00"))
        self.assertEqual(self.cat.known_tiles("mgrs"), {"T15TVH"})

    def test_import_hls_stamps_a_month_missing_a_sensor_with_its_end_date(self):
        store = OldStore(self.tmp / "hls.sqlite")
        store.replace_month("S30", "2025-08", [
            {"id": "HLS.S30.T15TVH.2025220T170000.v2.0", "tile": "T15TVH", "date": "2025-08-08", "time": "2025-08-08T17:00:00Z", "sensor": "S30", "cloud": 5},
        ], "2025-09-05T00:00:00+00:00")
        store.close()
        migrate.import_hls(self.cat, self.tmp / "hls.sqlite", now=NOW, out=self.out)
        self.assertEqual(self.cat.fetched_at("hls", "2025-08"), "2025-09-01T00:00:00+00:00")
        self.assertEqual(self.cat.fetched_at("hls", "2025-07"), "2025-08-01T00:00:00+00:00")

    def test_import_footprints_drops_baked_pairing_and_records_months(self):
        os.utime(self.tmp / "emit.geojson", (1750000000, 1750000000))
        counts = migrate.import_footprints(self.cat, self.tmp / "emit.geojson", self.reg.mission("emit"), out=self.out)
        self.assertEqual(counts, {"granules": 1, "months": 1})
        row = self.cat.conn.execute("SELECT * FROM granules WHERE mission='emit'").fetchone()
        self.assertEqual((row["cloud"], row["browse"], row["data"], row["attrs"]), (12.0, "https://x/EMIT_1.png", "https://x/EMIT_1.nc", None))
        self.assertEqual((row["minlon"], row["maxlat"]), (-94.0, 42.5))
        self.assertEqual(self.cat.fetched_at("emit", "2025-07"), "2025-06-15T15:06:40+00:00")
        counts = migrate.import_footprints(self.cat, self.tmp / "eco.geojson", self.reg.mission("eco"), out=self.out)
        row = self.cat.conn.execute("SELECT daynight, orbit FROM granules WHERE mission='eco'").fetchone()
        self.assertEqual((row["daynight"], row["orbit"]), ("DAY", 39607))

    def test_migrate_is_idempotent_and_computes_coverage(self):
        args = ["--registry", str(paths.MISSIONS), "--catalog", str(self.tmp / "m.sqlite"),
                "--hls", str(self.tmp / "hls.sqlite"), "--emit", str(self.tmp / "emit.geojson"), "--eco", str(self.tmp / "eco.geojson")]
        with unittest.mock.patch("sys.stdout", self.out):
            self.assertEqual(migrate.main(args), 0)
            self.assertEqual(migrate.main(args), 0)
        cat = catalog.open_read_only(self.tmp / "m.sqlite")
        try:
            self.assertEqual(cat.conn.execute("SELECT COUNT(*) FROM granules").fetchone()[0], 5)
            self.assertEqual(cat.conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0], 1)
            self.assertEqual(cat.conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0], 2)     # EMIT and ECO both touch T15TVH
            self.assertEqual(cat.summary("hls")["count"], 3)
        finally:
            cat.close()

    def test_missing_sources_are_reported_and_skipped(self):
        with unittest.mock.patch("sys.stdout", self.out), unittest.mock.patch("sys.stderr", io.StringIO()) as err:
            code = migrate.main(["--catalog", str(self.tmp / "m.sqlite"), "--hls", str(self.tmp / "none.sqlite"),
                                 "--emit", str(self.tmp / "none.geojson"), "--eco", str(self.tmp / "none2.geojson")])
        self.assertEqual(code, 0)
        self.assertIn("none.sqlite", err.getvalue())
