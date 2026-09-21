"""Fetch every EMIT L2A reflectance footprint over CONUS from NASA's CMR.

Run by ./run.sh emit. This is one of the project's few sanctioned network
steps; the interface never touches the network. Each run refetches in full
(about fifteen requests of 2,000 granules) and rewrites the file atomically.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from viz import paths

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
SHORT_NAME = "EMITL2ARFL"
BBOX = "-125,24.4,-66.9,49.4"
PAGE_SIZE = 2000

_BROWSE = "/browse#"
_DATA = "/data#"


def _link(entry, suffix):
    for link in entry.get("links", []):
        href = link.get("href", "")
        if link.get("rel", "").endswith(suffix) and href.startswith("http"):
            return href
    return None


def entry_to_feature(entry):
    """One CMR granule entry to one GeoJSON feature, or None without a polygon."""
    polygons = entry.get("polygons")
    if not polygons or not polygons[0]:
        return None
    numbers = [float(v) for v in polygons[0][0].split()]
    # CMR lists latitude then longitude; GeoJSON wants longitude then latitude.
    ring = [[numbers[i + 1], numbers[i]] for i in range(0, len(numbers), 2)]
    if ring[0] != ring[-1]:
        ring.append(ring[0])

    start = entry.get("time_start")
    cloud = entry.get("cloud_cover")
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {
            "id": entry.get("title"),
            "start": start,
            "end": entry.get("time_end"),
            "cloud": float(cloud) if cloud not in (None, "") else None,
            "year": int(start[:4]) if start else None,
            "browse": _link(entry, _BROWSE),
            "data": _link(entry, _DATA),
        },
    }


def fetch_page(page_num):
    """Entries from one CMR page. Retries transient failures three times."""
    url = (f"{CMR_URL}?short_name={SHORT_NAME}&bounding_box={BBOX}"
           f"&page_size={PAGE_SIZE}&page_num={page_num}")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return json.load(response)["feed"]["entry"]
        except (OSError, ValueError, KeyError) as exc:
            if attempt == 2:
                raise
            print(f"page {page_num}: {exc}; retrying", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def fetch_all(fetch_page_fn):
    """Every entry, paging until a page comes back empty."""
    entries = []
    page = 1
    while True:
        got = fetch_page_fn(page)
        if not got:
            return entries
        entries.extend(got)
        print(f"page {page}: {len(got)} granules (total {len(entries)})", flush=True)
        page += 1


def write_geojson(features, path):
    """Write a FeatureCollection via a .part file and an atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    os.replace(part, path)


def main(argv=None):
    entries = fetch_all(fetch_page)
    features = [f for f in (entry_to_feature(e) for e in entries) if f is not None]
    write_geojson(features, paths.EMIT_FOOTPRINTS)
    print(f"emit: {len(features)} footprints written to {paths.EMIT_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
