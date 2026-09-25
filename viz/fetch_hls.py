"""Fetch HLS (HLSL30 and HLSS30 v2.0) granule metadata over CONUS into SQLite.

Run by ./run.sh hls (and as the last step of ./run.sh footprints). Each
collection-month is one CSV query paged by the CMR-Hits header, because CMR
caps paging depth at one million rows per query and the JSON endpoint is
five times heavier than CSV. Months fetched well after they ended are frozen
and skipped, so a rerun touches only recent months. Tile outlines are
computed from the tile IDs (viz.hls.tile_ring) and rewritten on every run,
which takes a second for the roughly 1,200 tiles that cover CONUS.
"""

import argparse
import datetime
import sys

from viz import cmr, hls, paths

SHORT_NAMES = {"L30": "HLSL30", "S30": "HLSS30"}
VERSION = "2.0"
CSV_URL = "https://cmr.earthdata.nasa.gov/search/granules.csv"


def csv_url(short_name, month, page_num):
    start, end = hls.month_bounds(month)
    return (f"{CSV_URL}?short_name={short_name}&version={VERSION}&bounding_box={cmr.BBOX}"
            f"&temporal={start}T00:00:00Z,{end}T00:00:00Z"
            f"&page_size={cmr.PAGE_SIZE}&page_num={page_num}")


def current_month():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def fetch_month(short_name, month, fetch_fn=cmr.fetch_response):
    """Every row of one collection-month, paged by CMR-Hits, limited to the month's dates.

    Raises ValueError if the response has no usable CMR-Hits header, or if the
    pages together deliver fewer parsed rows than CMR-Hits promised, so neither
    a malformed response nor a short page is recorded as a completed fetch and
    frozen. A row parse_csv drops for an unreadable granule UR counts as a
    short delivery too.
    """
    url = csv_url(short_name, month, 1)
    body, headers = fetch_fn(url)
    try:
        hits = int(headers.get("CMR-Hits"))
    except (TypeError, ValueError):
        raise ValueError(f"no CMR-Hits header in the response for {url}") from None
    rows = hls.parse_csv(body.decode("utf-8"))
    pages = -(-hits // cmr.PAGE_SIZE)
    for page in range(2, pages + 1):
        body, _ = fetch_fn(csv_url(short_name, month, page))
        rows.extend(hls.parse_csv(body.decode("utf-8")))
    if len(rows) < hits:
        raise ValueError(f"{short_name} {month}: CMR reported {hits} granules "
                         f"but delivered {len(rows)}")
    start, end = hls.month_bounds(month)
    return [r for r in rows if start <= r["date"] < end]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch HLS granule metadata into SQLite.")
    parser.add_argument("--from", dest="first", default=hls.FIRST_MONTH,
                        help="first month to fetch, YYYY-MM (default 2022-01)")
    args = parser.parse_args(argv)

    store = hls.Store(paths.HLS_DB)
    try:
        months = hls.months_between(args.first, current_month())
        for sensor, short_name in SHORT_NAMES.items():
            for month in months:
                fetched = store.fetched_at(sensor, month)
                if fetched and hls.is_frozen(month, fetched):
                    continue
                rows = fetch_month(short_name, month)
                store.replace_month(sensor, month, rows, now_iso())
                print(f"hls {sensor} {month}: {len(rows)} granules", flush=True)

        tiles = store.distinct_tiles()
        outlines = []
        for tile in tiles:
            ring = hls.tile_ring(tile)
            if ring is None:
                print(f"hls: cannot place tile {tile}; it is counted but not drawn", file=sys.stderr)
                continue
            outlines.append((tile, ring))
        store.put_tiles(outlines)
        print(f"hls tiles: {len(outlines)} outlines computed", flush=True)

        summary = store.summary()
        print(f"hls: {summary['count']} granules, {summary['tiles']} tiles in {paths.HLS_DB}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
