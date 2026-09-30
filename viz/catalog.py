"""The per-region catalog: one SQLite file holding every mission's granules.

Written by viz/refresh.py and viz/migrate.py; read by the server through
short-timeout read-only connections that raise BUSY while a refresh commits,
so a route can degrade instead of stalling. Schema per the mission registry
design, section 3.
"""

import json
import os
import sqlite3
import threading
from pathlib import Path

from viz import spatial
from viz.months import month_bounds

BUSY = sqlite3.OperationalError
ATTRIBUTE_COLUMNS = ("cloud", "daynight", "sensor", "orbit")
_ROW_COLUMNS = ("id", "start", "end", "cloud", "daynight", "sensor", "orbit", "tile",
                "minlon", "minlat", "maxlon", "maxlat", "ring", "browse", "data", "attrs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS granules (
  mission TEXT NOT NULL,
  id TEXT NOT NULL,
  start TEXT NOT NULL,
  end TEXT,
  cloud REAL,
  daynight TEXT,
  sensor TEXT,
  orbit INTEGER,
  tile TEXT,
  minlon REAL, minlat REAL, maxlon REAL, maxlat REAL,
  ring TEXT,
  browse TEXT,
  data TEXT,
  attrs TEXT,
  PRIMARY KEY (mission, id)
);
CREATE INDEX IF NOT EXISTS granules_mission_start ON granules(mission, start);
CREATE INDEX IF NOT EXISTS granules_mission_tile ON granules(mission, tile, start, cloud);
CREATE INDEX IF NOT EXISTS granules_mission_lat ON granules(mission, minlat, maxlat, start);
CREATE INDEX IF NOT EXISTS granules_mission_start_cloud_tile ON granules(mission, start, cloud, tile);
CREATE INDEX IF NOT EXISTS granules_mission_sensor_start ON granules(mission, sensor, start, cloud, tile);
CREATE TABLE IF NOT EXISTS tiles (
  grid TEXT NOT NULL,
  tile TEXT NOT NULL,
  ring TEXT NOT NULL,
  PRIMARY KEY (grid, tile)
);
CREATE TABLE IF NOT EXISTS coverage (
  mission TEXT NOT NULL,
  id TEXT NOT NULL,
  grid TEXT NOT NULL,
  tile TEXT NOT NULL,
  PRIMARY KEY (mission, id, grid, tile)
);
CREATE INDEX IF NOT EXISTS coverage_grid_tile ON coverage(grid, tile, mission);
CREATE TABLE IF NOT EXISTS months (
  mission TEXT NOT NULL,
  month TEXT NOT NULL,
  count INTEGER NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (mission, month)
);
"""


def _values(mission, row):
    attrs = dict(row.get("attrs") or {})
    attrs.update({k: v for k, v in row.items() if k not in _ROW_COLUMNS and k != "mission"})
    out = [mission]
    for column in _ROW_COLUMNS:
        value = row.get(column)
        if column == "attrs":
            value = attrs or None
        if column in ("ring", "attrs") and value is not None:
            value = json.dumps(value)
        out.append(value)
    return out


class Catalog:
    """One connection to a region's catalog; writable by default, read-only for the server."""

    def __init__(self, path, read_only=False):
        self.path = Path(path)
        if read_only:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=0.5)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path))
            self.conn.executescript(SCHEMA)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def bulk_mode(self):
        """Favor write speed over crash safety for a long import or refresh.

        Sets synchronous=OFF, an in-memory journal, and a 256 MB page cache.
        This trades crash safety for speed on a catalog that is always
        rebuildable from CMR; a refresh interrupted mid-month may need to be
        rerun, and a crash can leave the file corrupt.
        """
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("PRAGMA journal_mode=MEMORY")
        self.conn.execute("PRAGMA cache_size=-262144")

    def analyze(self):
        """Record planner statistics so grouped range queries pick the covering start indexes."""
        self.conn.execute("ANALYZE")
        self.conn.commit()

    # --- writes ---

    def replace_month(self, mission, month, rows, fetched_at):
        """Replace one mission-month in a single transaction and record the fetch."""
        start, end = month_bounds(month)
        marks = ",".join("?" * (len(_ROW_COLUMNS) + 1))
        with self.conn:
            self.conn.execute("DELETE FROM coverage WHERE mission = ? AND id IN "
                              "(SELECT id FROM granules WHERE mission = ? AND start >= ? AND start < ?)",
                              (mission, mission, start, end))
            self.conn.execute("DELETE FROM granules WHERE mission = ? AND start >= ? AND start < ?",
                              (mission, start, end))
            self.conn.executemany(
                f"INSERT OR REPLACE INTO granules (mission, {', '.join(_ROW_COLUMNS)}) VALUES ({marks})",
                [_values(mission, r) for r in rows])
            self.conn.execute(
                "INSERT OR REPLACE INTO months (mission, month, count, fetched_at) VALUES (?, ?, ?, ?)",
                (mission, month, len(rows), fetched_at))

    def put_tiles(self, grid, pairs):
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO tiles (grid, tile, ring) VALUES (?, ?, ?)",
                                  [(grid, tile, json.dumps(ring)) for tile, ring in pairs])

    def put_coverage(self, rows):
        with self.conn:
            self.conn.executemany("INSERT OR IGNORE INTO coverage (mission, id, grid, tile) VALUES (?, ?, ?, ?)", rows)

    # --- reads ---

    def fetched_at(self, mission, month):
        row = self.conn.execute("SELECT fetched_at FROM months WHERE mission = ? AND month = ?",
                                (mission, month)).fetchone()
        return row["fetched_at"] if row else None

    def distinct_tiles(self, mission):
        return [r["tile"] for r in self.conn.execute(
            "SELECT DISTINCT tile FROM granules WHERE mission = ? AND tile IS NOT NULL ORDER BY tile", (mission,))]

    def known_tiles(self, grid):
        return {r["tile"] for r in self.conn.execute("SELECT tile FROM tiles WHERE grid = ?", (grid,))}

    def tile_rings(self, grid):
        return [(r["tile"], json.loads(r["ring"])) for r in self.conn.execute(
            "SELECT tile, ring FROM tiles WHERE grid = ? ORDER BY tile", (grid,))]

    def uncovered(self, mission, grid):
        """(id, box, ring) of the mission's granules with no coverage row for the grid."""
        sql = ("SELECT g.id, g.minlon, g.minlat, g.maxlon, g.maxlat, g.ring FROM granules g "
               "WHERE g.mission = ? AND g.ring IS NOT NULL AND NOT EXISTS ("
               "SELECT 1 FROM coverage c WHERE c.mission = g.mission AND c.id = g.id AND c.grid = ?) ORDER BY g.id")
        return [(r["id"], (r["minlon"], r["minlat"], r["maxlon"], r["maxlat"]), json.loads(r["ring"]))
                for r in self.conn.execute(sql, (mission, grid))]

    def summary(self, mission):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(count), 0) AS n, MAX(fetched_at) AS f, COUNT(*) AS m FROM months WHERE mission = ?",
            (mission,)).fetchone()
        return {"count": row["n"], "fetched": row["f"], "months": row["m"]}

    def missions_present(self):
        return [r["mission"] for r in self.conn.execute("SELECT DISTINCT mission FROM granules ORDER BY mission")]


def open_read_only(path):
    """A read-only Catalog; FileNotFoundError when the file is absent (never create one by accident)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return Catalog(path, read_only=True)


def is_busy(exc):
    """True for the lock errors a refresh causes; any other OperationalError is a fault."""
    text = str(exc)
    return "locked" in text or "busy" in text


_local = threading.local()


def _drop_cached():
    cached = getattr(_local, "entry", None)
    _local.entry = None
    if cached is not None:
        cached[1][0].close()


def catalog_for(path):
    """(Catalog, {grid: RingIndex of tile ids}) for this thread, read-only, reopened when the file changes.

    None when the file is absent. Raises the lock error when building the tile
    indexes hits a refresh's write lock, after closing the connection.
    """
    path = Path(path)
    if not path.is_file():
        _drop_cached()
        return None
    key = (str(path), os.stat(path).st_mtime_ns)
    cached = getattr(_local, "entry", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    _drop_cached()
    cat = Catalog(path, read_only=True)
    try:
        grids = [r["grid"] for r in cat.conn.execute("SELECT DISTINCT grid FROM tiles ORDER BY grid")]
        indexes = {grid: spatial.RingIndex((ring, tile) for tile, ring in cat.tile_rings(grid)) for grid in grids}
    except Exception:
        cat.close()
        raise
    _local.entry = (key, (cat, indexes))
    return cat, indexes
