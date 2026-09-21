"""Fetch HLS (HLSL30 and HLSS30 v2.0) granule metadata over CONUS into SQLite.

Run by ./run.sh hls (and as the last step of ./run.sh footprints). Each
collection-month is one CSV query paged by the CMR-Hits header, because CMR
caps paging depth at one million rows per query and the JSON endpoint is
five times heavier than CSV. Months fetched well after they ended are frozen
and skipped, so a rerun touches only recent months. Tile rings are filled
once per tile from a JSON pattern query: about 1,200 tiles cover CONUS, so a
first run spends roughly ten minutes on them and later runs none.
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


def tile_urls(tile):
    """One pattern query per collection, S30 first; either returns the tile's ring."""
    return [
        f"{cmr.CMR_URL}?short_name={SHORT_NAMES[sensor]}&version={VERSION}"
        f"&granule_ur=HLS.{sensor}.{tile}.*&options[granule_ur][pattern]=true&page_size=1"
        for sensor in ("S30", "L30")
    ]


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


def fetch_ring(tile, fetch_page_fn=cmr.fetch_page):
    """The tile's closed [lon, lat] ring from the first collection that has one, or None."""
    for url in tile_urls(tile):
        for entry in fetch_page_fn(url):
            ring = cmr.polygon_ring(entry)
            if ring:
                return ring
    return None


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

        missing = store.missing_tiles()
        for i, tile in enumerate(missing, 1):
            ring = fetch_ring(tile)
            if ring is None:
                print(f"hls: no polygon for {tile}; it is counted but not drawn", file=sys.stderr)
                continue
            store.put_tile(tile, ring)
            if i % 50 == 0 or i == len(missing):
                print(f"hls tiles: {i}/{len(missing)}", flush=True)

        summary = store.summary()
        print(f"hls: {summary['count']} granules, {summary['tiles']} tiles in {paths.HLS_DB}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
