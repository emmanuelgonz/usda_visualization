"""HLS acquisition store: granule parsing, month arithmetic, and the SQLite tables.

Data lives in data/hls/hls.sqlite (stdlib sqlite3). viz/fetch_hls.py writes
through Store.replace_month and Store.put_tile; the server reads through a
per-thread read-only Store from store_for(). Months are the unit of fetching
because CMR caps paging depth at one million rows per query.
"""

import csv
import datetime
import io
import re

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
