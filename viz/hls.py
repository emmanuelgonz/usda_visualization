"""HLS acquisition store: granule parsing, month arithmetic, and the SQLite tables.

Data lives in data/hls/hls.sqlite (stdlib sqlite3). viz/fetch_hls.py writes
through Store.replace_month and Store.put_tile; the server reads through a
per-thread read-only Store from store_for(). Months are the unit of fetching
because CMR caps paging depth at one million rows per query.
"""

import csv
import datetime
import io
import json
import os
import re
import sqlite3
import threading
from pathlib import Path

from viz.emit import point_in_ring

SENSORS = ("L30", "S30")
FIRST_MONTH = "2022-01"
FROZEN_AFTER_DAYS = 60

_UR_RE = re.compile(r"^HLS\.(L30|S30)\.(T[0-9]{2}[A-Z]{3})\.")


def parse_ur(granule_ur):
    """('S30', 'T15TVH') from 'HLS.S30.T15TVH.2025203T170849.v2.0'; None if malformed."""
    match = _UR_RE.match(granule_ur or "")
    return (match.group(1), match.group(2)) if match else None


def months_between(first, last):
    """Every 'YYYY-MM' from first to last inclusive."""
    year, month = (int(v) for v in first.split("-"))
    last_year, last_month = (int(v) for v in last.split("-"))
    out = []
    while (year, month) <= (last_year, last_month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def month_bounds(month):
    """(first day of the month, first day of the next month) as ISO dates."""
    year, mon = (int(v) for v in month.split("-"))
    start = datetime.date(year, mon, 1)
    end = datetime.date(year + 1, 1, 1) if mon == 12 else datetime.date(year, mon + 1, 1)
    return start.isoformat(), end.isoformat()


def is_frozen(month, fetched_at, days=FROZEN_AFTER_DAYS):
    """True when the month was fetched at least `days` after it ended."""
    _, end = month_bounds(month)
    end_date = datetime.date.fromisoformat(end)
    fetched = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).date()
    return (fetched - end_date).days >= days


def parse_csv(text):
    """Rows {id, tile, date, time, sensor, cloud} from a CMR granules.csv body.

    Rows whose granule UR is not an HLS UR, or whose start time is blank, are
    dropped. A blank or non-numeric cloud cover becomes None.
    """
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        ur = record.get("Granule UR") or ""
        parsed = parse_ur(ur)
        start = record.get("Start Time") or ""
        if parsed is None or len(start) < 10:
            continue
        sensor, tile = parsed
        try:
            cloud = int(float(record.get("Cloud Cover") or ""))
        except ValueError:
            cloud = None
        rows.append({"id": ur, "tile": tile, "date": start[:10], "time": start,
                     "sensor": sensor, "cloud": cloud})
    return rows


SCHEMA = """
CREATE TABLE IF NOT EXISTS acq (
  id TEXT PRIMARY KEY,
  tile TEXT NOT NULL,
  date TEXT NOT NULL,
  time TEXT NOT NULL,
  sensor TEXT NOT NULL,
  cloud INTEGER
);
CREATE INDEX IF NOT EXISTS acq_date ON acq(date);
CREATE INDEX IF NOT EXISTS acq_tile_date ON acq(tile, date);
CREATE TABLE IF NOT EXISTS tiles (
  tile TEXT PRIMARY KEY,
  ring TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS months (
  sensor TEXT NOT NULL,
  month TEXT NOT NULL,
  count INTEGER NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (sensor, month)
);
"""


class Store:
    """One connection to the HLS database.

    Writable by default (creates the file and schema); read_only for the
    server, which must never create an empty database by accident.
    """

    def __init__(self, path, read_only=False):
        self.path = Path(path)
        if read_only:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path))
            self.conn.executescript(SCHEMA)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    # --- writes (fetch script) ---

    def replace_month(self, sensor, month, rows, fetched_at):
        """Replace one sensor-month in a single transaction and record the fetch."""
        start, end = month_bounds(month)
        with self.conn:
            self.conn.execute("DELETE FROM acq WHERE sensor = ? AND date >= ? AND date < ?",
                              (sensor, start, end))
            self.conn.executemany(
                "INSERT OR REPLACE INTO acq (id, tile, date, time, sensor, cloud) VALUES (?, ?, ?, ?, ?, ?)",
                [(r["id"], r["tile"], r["date"], r["time"], r["sensor"], r["cloud"]) for r in rows])
            self.conn.execute(
                "INSERT OR REPLACE INTO months (sensor, month, count, fetched_at) VALUES (?, ?, ?, ?)",
                (sensor, month, len(rows), fetched_at))

    def fetched_at(self, sensor, month):
        row = self.conn.execute("SELECT fetched_at FROM months WHERE sensor = ? AND month = ?",
                                (sensor, month)).fetchone()
        return row["fetched_at"] if row else None

    def missing_tiles(self):
        """Tiles present in acq but without a ring, sorted."""
        return [r["tile"] for r in self.conn.execute(
            "SELECT DISTINCT tile FROM acq WHERE tile NOT IN (SELECT tile FROM tiles) ORDER BY tile")]

    def put_tile(self, tile, ring):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO tiles (tile, ring) VALUES (?, ?)",
                              (tile, json.dumps(ring)))

    # --- reads (server) ---

    def counts(self, start, end, cloud, sensor="ALL"):
        """Clear acquisitions per tile over an inclusive date range. NULL cloud never counts."""
        sql = "SELECT tile, COUNT(*) AS n FROM acq WHERE date BETWEEN ? AND ? AND cloud <= ?"
        args = [start, end, cloud]
        if sensor != "ALL":
            sql += " AND sensor = ?"
            args.append(sensor)
        sql += " GROUP BY tile"
        return {r["tile"]: r["n"] for r in self.conn.execute(sql, args)}

    def acquisitions(self, tiles, start, end):
        """Every acquisition of the given tiles in the range, oldest first."""
        tiles = list(tiles)
        if not tiles:
            return []
        marks = ",".join("?" * len(tiles))
        sql = (f"SELECT tile, date, time, sensor, cloud FROM acq WHERE tile IN ({marks}) "
               "AND date BETWEEN ? AND ? ORDER BY time, tile")
        return [dict(r) for r in self.conn.execute(sql, [*tiles, start, end])]

    def tiles_geojson(self):
        features = [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [json.loads(r["ring"])]},
            "properties": {"tile": r["tile"]},
        } for r in self.conn.execute("SELECT tile, ring FROM tiles ORDER BY tile")]
        return {"type": "FeatureCollection", "features": features}

    def summary(self):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(count), 0) AS n, MAX(fetched_at) AS f FROM months").fetchone()
        tiles = self.conn.execute("SELECT COUNT(*) AS n FROM tiles").fetchone()["n"]
        return {"count": row["n"], "fetched": row["f"], "tiles": tiles}


class TileIndex:
    """Point-in-tile over the tiles table. MGRS tiles overlap, so a point can hit several."""

    def __init__(self, store):
        self._items = []
        for r in store.conn.execute("SELECT tile, ring FROM tiles"):
            ring = json.loads(r["ring"])
            lons = [p[0] for p in ring]
            lats = [p[1] for p in ring]
            self._items.append(((min(lons), min(lats), max(lons), max(lats)), ring, r["tile"]))

    def covering(self, lon, lat):
        return sorted(
            tile for (minx, miny, maxx, maxy), ring, tile in self._items
            if minx <= lon <= maxx and miny <= lat <= maxy and point_in_ring(lon, lat, ring)
        )


_local = threading.local()


def store_for(path):
    """(Store, TileIndex) for this thread, read-only, reopened when the file changes; None if absent."""
    path = Path(path)
    if not path.is_file():
        return None
    key = (str(path), os.stat(path).st_mtime_ns)
    cached = getattr(_local, "entry", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    if cached is not None:
        cached[1][0].close()
    store = Store(path, read_only=True)
    entry = (store, TileIndex(store))
    _local.entry = (key, entry)
    return entry


def is_clear(row, cloud, sensor="ALL"):
    """Clear means a known cloud value at or under the threshold, from an accepted sensor."""
    if row["cloud"] is None or row["cloud"] > cloud:
        return False
    return sensor == "ALL" or row["sensor"] == sensor


def nearest_clear(rows, emit_date, window):
    """The row nearest emit_date within ±window days, ties to the earlier time; None if none.

    rows must already be filtered with is_clear. dt is HLS date minus EMIT date
    in whole calendar days.
    """
    base = datetime.date.fromisoformat(emit_date)
    best = None
    for row in rows:
        dt = (datetime.date.fromisoformat(row["date"]) - base).days
        if abs(dt) > window:
            continue
        key = (abs(dt), row["time"])
        if best is None or key < best[0]:
            best = (key, {"date": row["date"], "sensor": row["sensor"], "cloud": row["cloud"], "dt": dt})
    return best[1] if best else None
