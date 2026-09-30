"""Tiled missions: one CSV query per collection-month, rows keyed by grid tile.

CSV is used because a tiled row needs only the id, time, cloud, and browse
link, and the CSV endpoint is a fifth the size of JSON per granule. CMR caps
paging depth at one million rows per query, so months are the unit.
"""

import csv
import datetime
import io
import re

from viz import cmr, filters
from viz.months import month_bounds

CSV_URL = "https://cmr.earthdata.nasa.gov/search/granules.csv"
_CSV_SOURCES = {"cloud_cover": "Cloud Cover", "day_night_flag": "Day/Night"}


def csv_url(collection, bbox, month, page_num):
    start, end = month_bounds(month)
    box = ",".join(str(v) for v in bbox)
    return (f"{CSV_URL}?short_name={collection.short_name}&version={collection.version}&bounding_box={box}"
            f"&temporal={start}T00:00:00Z,{end}T00:00:00Z&page_size={cmr.PAGE_SIZE}&page_num={page_num}")


def _number(text, kind):
    if text in (None, ""):
        return None
    try:
        return int(float(text)) if kind == "int" else float(text)
    except ValueError:
        return None


def _attribute(spec, record, collection):
    if spec.source in collection.implies:
        value = collection.implies[spec.source]
    else:
        value = record.get(_CSV_SOURCES.get(spec.source, spec.source))
    if spec.type in ("number", "int"):
        return _number(value, spec.type)
    return value or None


def _browse(mission, record):
    if mission.browse.get("source") != "links":
        return None
    match = re.compile(mission.browse["match"])
    for url in (record.get("Browse URLs") or "").split(","):
        url = url.strip()
        if url.startswith("http") and match.search(url):
            return url
    return None


def parse_csv(mission, collection, text):
    """Catalog rows from a CMR granules.csv body; rows without a tile or start time are dropped."""
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        title = record.get("Granule UR") or ""
        match = mission.tile_from.pattern.match(title)
        start = record.get("Start Time") or ""
        if match is None or len(start) < 10:
            continue
        row = {"id": title, "start": start, "end": record.get("End Time") or start,
               "tile": match.group(1), "browse": _browse(mission, record)}
        for name, spec in mission.attributes.items():
            row[name] = _attribute(spec, record, collection)
        rows.append(row)
    return rows


def fetch_month(mission, collection, bbox, month, fetch_fn=cmr.fetch_response):
    """Every row of one collection-month, paged by CMR-Hits, limited to the month's dates.

    Raises ValueError without a usable CMR-Hits header, or when the pages
    deliver fewer parsed rows than CMR-Hits promised, so a short page is never
    recorded as a completed fetch and frozen.
    """
    url = csv_url(collection, bbox, month, 1)
    body, headers = fetch_fn(url)
    try:
        hits = int(headers.get("CMR-Hits"))
    except (TypeError, ValueError):
        raise ValueError(f"no CMR-Hits header in the response for {url}") from None
    rows = parse_csv(mission, collection, body.decode("utf-8"))
    pages = -(-hits // cmr.PAGE_SIZE)
    for page in range(2, pages + 1):
        body, _ = fetch_fn(csv_url(collection, bbox, month, page))
        rows.extend(parse_csv(mission, collection, body.decode("utf-8")))
    if len(rows) < hits:
        raise ValueError(f"{collection.short_name} {month}: CMR reported {hits} granules but delivered {len(rows)}")
    start, end = month_bounds(month)
    return [r for r in rows if start <= r["start"][:10] < end]


def counts(cat, mission, start, end, values):
    """Clear acquisitions per tile over the inclusive date range, filters applied in SQL; zero tiles omitted.

    Column names come from the registry, never from the request; values are bound.
    The range on start (not substr) keeps the start indexes usable; the planner
    needs the statistics Catalog.analyze() records to pick them over the tile index.
    """
    stop = (datetime.date.fromisoformat(end) + datetime.timedelta(days=1)).isoformat()
    where = ["mission = ?", "tile IS NOT NULL", "start >= ?", "start < ?"]
    args = [mission.key, start, stop]
    for control in mission.filters:
        value = values.get(control.attribute, control.default)
        if control.control == "max":
            where.append(f"{control.attribute} <= ?")
            args.append(value)
        elif value != "ALL":
            where.append(f"{control.attribute} = ?")
            args.append(value)
    sql = "SELECT tile, COUNT(*) AS n FROM granules WHERE " + " AND ".join(where) + " GROUP BY tile"
    return {row["tile"]: row["n"] for row in cat.conn.execute(sql, args)}


def acquisitions(cat, mission, tiles, start, end):
    """Every acquisition of the given tiles in the inclusive date range, oldest first then by tile."""
    tiles = list(tiles)
    if not tiles:
        return []
    marks = ",".join("?" * len(tiles))
    sql = (f"SELECT tile, start, cloud, sensor, daynight, orbit FROM granules WHERE mission = ? "
           f"AND tile IN ({marks}) AND substr(start, 1, 10) BETWEEN ? AND ? ORDER BY start, tile")
    rows = []
    for row in cat.conn.execute(sql, [mission.key, *tiles, start, end]):
        item = {"tile": row["tile"], "date": row["start"][:10], "time": row["start"]}
        for name in mission.attributes:
            item[name] = row[name] if name in row.keys() else None
        rows.append(item)
    return rows


def tiles_geojson(cat, grid):
    features = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                 "properties": {"tile": tile}} for tile, ring in cat.tile_rings(grid)]
    return {"type": "FeatureCollection", "features": features}


def nearest_clear(rows, date, window):
    """The row nearest the date within ±window days, ties to the earlier time; None if none.

    rows must already be filtered. dt is the acquisition date minus the given
    date in whole calendar days.
    """
    base = datetime.date.fromisoformat(date)
    best = None
    for row in rows:
        dt = (datetime.date.fromisoformat(row["date"]) - base).days
        if abs(dt) > window:
            continue
        key = (abs(dt), row["time"])
        if best is None or key < best[0]:
            best = (key, {"date": row["date"], "sensor": row.get("sensor"), "cloud": row.get("cloud"), "dt": dt})
    return best[1] if best else None
