"""Fetch every EMIT L2A reflectance footprint over CONUS from NASA's CMR.

Run by ./run.sh footprints (and its alias ./run.sh emit). Each run refetches
in full, about fifteen requests of 2,000 granules, and rewrites the file
atomically. Coincidence with ECOSTRESS is computed afterwards by
viz/coincidence.py.
"""

import sys

from viz import cmr, paths
from viz.cmr import fetch_all, write_geojson  # re-exported for tests and callers

SHORT_NAME = "EMITL2ARFL"

_BROWSE = "/browse#"
_DATA = "/data#"


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
            "browse": cmr.link(entry, _BROWSE),
            "data": cmr.link(entry, _DATA),
        },
    }


def fetch_page(page_num):
    return cmr.fetch_page(cmr.page_url(SHORT_NAME, page_num))


def main(argv=None):
    entries = fetch_all(fetch_page)
    features = [f for f in (entry_to_feature(e) for e in entries) if f is not None]
    write_geojson(features, paths.EMIT_FOOTPRINTS)
    print(f"emit: {len(features)} footprints written to {paths.EMIT_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
