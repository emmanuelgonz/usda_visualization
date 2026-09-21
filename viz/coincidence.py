"""Pair each EMIT scene with the ECOSTRESS swaths that overlap it in time and space.

Both instruments ride the ISS, so a same-pass pair is the common case: EMIT's
75 km swath lies inside ECOSTRESS's 400 km swath and the two starts are seconds
apart. A pair is recorded when the ECOSTRESS box intersects the EMIT scene's
bounding box within 24 hours either way. Pairs are written onto the EMIT
feature as its "eco" property, nearest in time first, so the interface can
filter by any smaller window without recomputing.
"""

import bisect
import datetime
import json
import sys

from viz import cmr, paths

MAX_DT = 86400      # seconds either side
CAP = 20            # pairs kept per EMIT scene
SAME_PASS = 900     # seconds; reported in the summary and the catalog


def ring_bbox(ring):
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lons), min(lats), max(lons), max(lats))


def intersects(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def parse_time(iso):
    """ISO-8601 with optional fractional seconds and a Z suffix to epoch seconds."""
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def pair(emit_features, eco_features, max_dt=MAX_DT, cap=CAP):
    """Attach eco pairs to every EMIT feature. Returns EMIT scenes paired within SAME_PASS."""
    eco = sorted(
        ((parse_time(f["properties"]["start"]), ring_bbox(f["geometry"]["coordinates"][0]), f["properties"])
         for f in eco_features),
        key=lambda item: item[0],
    )
    starts = [item[0] for item in eco]

    same_pass = 0
    for feature in emit_features:
        t0 = parse_time(feature["properties"]["start"])
        box = ring_bbox(feature["geometry"]["coordinates"][0])
        lo = bisect.bisect_left(starts, t0 - max_dt)
        hi = bisect.bisect_right(starts, t0 + max_dt)
        hits = []
        for t, eco_box, props in eco[lo:hi]:
            if intersects(box, eco_box):
                hits.append({"id": props["id"], "start": props["start"],
                             "daynight": props.get("daynight"), "dt": int(round(t - t0))})
        hits.sort(key=lambda h: abs(h["dt"]))
        feature["properties"]["eco"] = hits[:cap]
        if hits and abs(hits[0]["dt"]) <= SAME_PASS:
            same_pass += 1
    return same_pass


def main(argv=None):
    if not paths.ECO_FOOTPRINTS.is_file():
        print(f"coincidence: no ECOSTRESS file at {paths.ECO_FOOTPRINTS}", file=sys.stderr)
        return 1
    emit_data = json.loads(paths.EMIT_FOOTPRINTS.read_text())
    eco_data = json.loads(paths.ECO_FOOTPRINTS.read_text())
    same_pass = pair(emit_data["features"], eco_data["features"])
    cmr.write_geojson(emit_data["features"], paths.EMIT_FOOTPRINTS)
    total = len(emit_data["features"])
    print(f"coincidence: {same_pass} of {total} EMIT scenes have an ECOSTRESS swath within "
          f"{SAME_PASS // 60} min; pairs written to {paths.EMIT_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
