"""Tiled missions: one CSV query per collection-month, rows keyed by grid tile.

CSV is used because a tiled row needs only the id, time, cloud, and browse
link, and the CSV endpoint is a fifth the size of JSON per granule. CMR caps
paging depth at one million rows per query, so months are the unit.
"""

import csv
import io
import re

from viz import cmr
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
