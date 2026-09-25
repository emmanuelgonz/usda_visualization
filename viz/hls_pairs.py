"""Attach the nearby HLS acquisitions to every EMIT scene.

Run after the HLS fetch by ./run.sh hls and ./run.sh footprints. For each
EMIT scene every MGRS tile containing the scene centroid is looked up (two
or four where tiles overlap by 9.8 km), and their acquisitions within
PAIR_DAYS either side of the scene date are written onto the feature as its
"hls_near" property, nearest in time first, one entry per date and sensor
with the lowest cloud value kept. The per-click "hls" tag
on /api/point is a different field. The interface marks ECOSTRESS + HLS
coincidence from this list for any smaller window, cloud threshold, or
sensor without a round trip. Mirrors viz/coincidence.py.
"""

import datetime
import json
import sys

from viz import cmr, hls, paths

PAIR_DAYS = 15     # days either side of the scene date kept on the feature
CAP = 12           # acquisitions kept per scene


def centroid(ring):
    """Mean of the ring's vertices, ignoring a closing duplicate."""
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def pair(emit_features, store, index, days=PAIR_DAYS, cap=CAP):
    """Attach hls lists to every EMIT feature. Returns the number of scenes with at least one."""
    paired = 0
    span = datetime.timedelta(days=days)
    for feature in emit_features:
        props = feature["properties"]
        lon, lat = centroid(feature["geometry"]["coordinates"][0])
        tiles = index.covering(lon, lat)
        hits = []
        if tiles and props.get("start"):
            base = datetime.date.fromisoformat(props["start"][:10])
            rows = store.acquisitions(tiles, (base - span).isoformat(), (base + span).isoformat())
            best = {}
            for row in rows:
                key = (row["date"], row["sensor"])
                # The same pass covers both overlapping tiles; keep the clearer granule.
                if key in best and (row["cloud"] is None or
                                    (best[key]["cloud"] is not None and best[key]["cloud"] <= row["cloud"])):
                    continue
                dt = (datetime.date.fromisoformat(row["date"]) - base).days
                best[key] = {"date": row["date"], "sensor": row["sensor"], "cloud": row["cloud"], "dt": dt,
                             "_t": row["time"]}
            hits = sorted(best.values(), key=lambda h: (abs(h["dt"]), h["_t"]))
            for h in hits:
                del h["_t"]
        props["hls_near"] = hits[:cap]
        if hits:
            paired += 1
    return paired


def main(argv=None):
    if not paths.HLS_DB.is_file():
        print(f"hls pairs: no HLS store at {paths.HLS_DB}; run ./run.sh hls first", file=sys.stderr)
        return 1
    if not paths.EMIT_FOOTPRINTS.is_file():
        print(f"hls pairs: no EMIT file at {paths.EMIT_FOOTPRINTS}; run ./run.sh footprints first",
              file=sys.stderr)
        return 1
    store = hls.Store(paths.HLS_DB, read_only=True)
    try:
        index = hls.TileIndex(store)
        emit_data = json.loads(paths.EMIT_FOOTPRINTS.read_text())
        paired = pair(emit_data["features"], store, index)
    finally:
        store.close()
    cmr.write_geojson(emit_data["features"], paths.EMIT_FOOTPRINTS)
    total = len(emit_data["features"])
    print(f"hls pairs: {paired} of {total} EMIT scenes have an HLS acquisition within "
          f"{PAIR_DAYS} days; lists written to {paths.EMIT_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
