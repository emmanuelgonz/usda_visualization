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

# Raised by a read when a fetch holds the write lock, so the server can degrade
# an HLS response without importing sqlite3 itself.
BUSY = sqlite3.OperationalError

_UR_RE = re.compile(r"^HLS\.(L30|S30)\.(T[0-9]{2}[A-Z]{3})\.")


# MGRS tile ID: T, UTM zone, latitude band, 100 km column letter, 100 km row letter.
_TILE_ID_RE = re.compile(r"^T(\d{2})([C-HJ-NP-X])([A-HJ-NP-Z])([A-HJ-NP-V])$")
_BANDS = "CDEFGHJKLMNPQRSTUVWX"                   # 8-degree bands from 80 S
_COLUMN_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"
TILE_SIDE_M = 109800.0                             # Sentinel-2 / HLS tile side
_transforms = {}


def _transforms_for(zone):
    """(UTM -> WGS84, WGS84 -> UTM) for a northern-hemisphere zone, built once."""
    if zone not in _transforms:
        from osgeo import osr
        osr.UseExceptions()
        utm = osr.SpatialReference()
        utm.ImportFromEPSG(32600 + zone)
        wgs = osr.SpatialReference()
        wgs.ImportFromEPSG(4326)
        for srs in (utm, wgs):
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        _transforms[zone] = (osr.CoordinateTransformation(utm, wgs), osr.CoordinateTransformation(wgs, utm))
    return _transforms[zone]


def tile_ring(tile):
    """The closed [lon, lat] outline of an MGRS/HLS tile computed from its ID, or None if malformed.

    A granule's CMR polygon is its data footprint, which at a swath edge is a
    clipped piece of the tile, so the outline is computed instead: the 100 km
    square's easting comes from the column letter, its northing from the row
    letter (offset by five rows in even zones) resolved to the latitude band,
    and the 109.8 km Sentinel-2 tile hangs from the square's north-west corner.
    Northern hemisphere only, which covers every HLS tile over CONUS.
    """
    match = _TILE_ID_RE.match(tile or "")
    if not match:
        return None
    zone = int(match.group(1))
    band, column, row = match.group(2), match.group(3), match.group(4)
    if not (1 <= zone <= 60) or band < "N":
        return None
    columns = _COLUMN_SETS[(zone - 1) % 3]
    if column not in columns:
        return None
    to_wgs, to_utm = _transforms_for(zone)
    easting = (columns.index(column) + 1) * 100000.0
    row_index = (_ROW_LETTERS.index(row) - (0 if zone % 2 else 5)) % 20
    northing = row_index * 100000.0
    band_min_lat = -80 + 8 * _BANDS.index(band)
    band_min_northing = to_utm.TransformPoint(-183.0 + 6 * zone, float(band_min_lat))[1]
    while northing < band_min_northing - 100000.0:   # a square may start just south of its band
        northing += 2000000.0
    band_max_northing = to_utm.TransformPoint(-183.0 + 6 * zone, float(band_min_lat + 8))[1]
    if northing > band_max_northing:                  # row letter inconsistent with the band
        return None
    top = northing + 100000.0
    corners = [(easting, top - TILE_SIDE_M), (easting + TILE_SIDE_M, top - TILE_SIDE_M),
               (easting + TILE_SIDE_M, top), (easting, top)]
    ring = [[round(x, 6), round(y, 6)] for x, y, _ in (to_wgs.TransformPoint(e, n) for e, n in corners)]
    return ring + [ring[0]]


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
CREATE INDEX IF NOT EXISTS acq_date_cloud_tile ON acq(date, cloud, tile);
CREATE INDEX IF NOT EXISTS acq_tile_date_cover ON acq(tile, date, cloud, sensor, time);
DROP INDEX IF EXISTS acq_tile_date;
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
    server, which must never create an empty database by accident. A read-only
    connection waits half a second for the write lock rather than the default
    five, so a request made while a fetch commits a month raises BUSY quickly
    enough for the route to degrade instead of stalling.
    """

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

    def distinct_tiles(self):
        """Every tile present in acq, sorted."""
        return [r["tile"] for r in self.conn.execute("SELECT DISTINCT tile FROM acq ORDER BY tile")]

    def put_tile(self, tile, ring):
        self.put_tiles([(tile, ring)])

    def put_tiles(self, pairs):
        """Write (tile, ring) pairs in one transaction; one commit, not one per tile."""
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO tiles (tile, ring) VALUES (?, ?)",
                                  [(tile, json.dumps(ring)) for tile, ring in pairs])

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


def _drop_cached():
    """Close this thread's cached store, clearing the slot first so a failed reopen leaves nothing behind."""
    cached = getattr(_local, "entry", None)
    _local.entry = None
    if cached is not None:
        cached[1][0].close()


def store_for(path):
    """(Store, TileIndex) for this thread, read-only, reopened when the file changes; None if absent.

    Raises BUSY when a fetch holds the write lock, since building the tile
    index reads the tiles table.
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
