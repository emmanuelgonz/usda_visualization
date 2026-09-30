"""Month arithmetic shared by the catalog refresh and the HLS store.

Months are the unit of fetching because CMR caps paging depth at one million
rows per query; a month fetched long after it ended is frozen and never
fetched again.
"""

import datetime

FROZEN_AFTER_DAYS = 60


def _split_month(month):
    year, mon = month.split("-")
    return int(year), int(mon)


def months_between(first, last):
    """Every 'YYYY-MM' from first to last inclusive."""
    year, month = _split_month(first)
    last_year, last_month = _split_month(last)
    out = []
    while (year, month) <= (last_year, last_month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def month_bounds(month):
    """(first day of the month, first day of the next month) as ISO dates."""
    year, mon = _split_month(month)
    start = datetime.date(year, mon, 1)
    end = datetime.date(year + 1, 1, 1) if mon == 12 else datetime.date(year, mon + 1, 1)
    return start.isoformat(), end.isoformat()


def is_frozen(month, fetched_at, days=FROZEN_AFTER_DAYS):
    """True when the month was fetched at least `days` after it ended (calendar days in UTC)."""
    _, end = month_bounds(month)
    end_date = datetime.date.fromisoformat(end)
    stamp = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(datetime.timezone.utc)
    return (stamp.date() - end_date).days >= days


def current_month():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
