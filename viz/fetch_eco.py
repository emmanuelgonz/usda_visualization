"""Fetch ECOSTRESS swath scene bounding boxes over CONUS from NASA's CMR.

Run by ./run.sh footprints. The swath product (ECO_L2_LSTE v002) has one
granule per acquisition but carries only a bounding box in CMR, about 6 x 5
degrees for a 52-second scene against a real swath of roughly 400 km; no
polygon and no cloud cover. Boxes are written as rectangle polygons and are
labelled as approximate in the interface. Fetched from 2022 onward to match
EMIT's span.
"""

import sys

from viz import cmr, paths

SHORT_NAME = "ECO_L2_LSTE"
VERSION = "002"
TEMPORAL = "2022-01-01T00:00:00Z,"   # open-ended: 2022 to now


def box_to_ring(box):
    """CMR 'south west north east' to a closed [lon, lat] rectangle ring."""
    south, west, north, east = (float(v) for v in box.split())
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def entry_to_feature(entry):
    """One CMR swath entry to one GeoJSON feature, or None without a box."""
    boxes = entry.get("boxes")
    if not boxes:
        return None
    start = entry.get("time_start")
    domains = entry.get("orbit_calculated_spatial_domains") or []
    orbit = domains[0].get("start_orbit_number") if domains else None
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [box_to_ring(boxes[0])]},
        "properties": {
            "id": entry.get("title"),
            "start": start,
            "end": entry.get("time_end"),
            "daynight": entry.get("day_night_flag"),
            "year": int(start[:4]) if start else None,
            "orbit": int(orbit) if orbit not in (None, "") else None,
        },
    }


def fetch_page(page_num):
    return cmr.fetch_page(cmr.page_url(SHORT_NAME, page_num, version=VERSION, temporal=TEMPORAL))


def main(argv=None):
    entries = cmr.fetch_all(fetch_page)
    features = [f for f in (entry_to_feature(e) for e in entries) if f is not None]
    cmr.write_geojson(features, paths.ECO_FOOTPRINTS)
    print(f"eco: {len(features)} swath boxes written to {paths.ECO_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
