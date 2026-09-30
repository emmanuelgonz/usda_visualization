"""Import the pre-registry stores into the region catalog, once.

Run by ./run.sh migrate. The HLS store (data/hls/hls.sqlite) and the EMIT
and ECOSTRESS footprint files are copied into data/catalog/<region>.sqlite so
nothing is fetched again; the baked pairing properties are dropped because
coincidence becomes a query. Safe to run more than once.
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from viz import catalog, months, paths, refresh, registry, spatial


def _months_of(rows):
    by_month = defaultdict(list)
    for row in rows:
        by_month[row["start"][:7]].append(row)
    return by_month


def import_hls(cat, store_path, mission_key="hls", now=months.now_iso, out=sys.stdout):
    """Copy acq rows, collapsed sensor months, and tile rings from the old HLS store."""
    conn = sqlite3.connect(f"file:{Path(store_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [{"id": r["id"], "start": r["time"], "end": r["time"], "tile": r["tile"], "sensor": r["sensor"],
                 "cloud": float(r["cloud"]) if r["cloud"] is not None else None}
                for r in conn.execute("SELECT id, tile, time, sensor, cloud FROM acq")]
        fetched = {}
        for r in conn.execute("SELECT month, MAX(fetched_at) AS f FROM months GROUP BY month"):
            fetched[r["month"]] = r["f"]
        tiles = [(r["tile"], json.loads(r["ring"])) for r in conn.execute("SELECT tile, ring FROM tiles")]
    finally:
        conn.close()
    by_month = _months_of(rows)
    for month, month_rows in sorted(by_month.items()):
        cat.replace_month(mission_key, month, month_rows, fetched.get(month) or now())
    cat.put_tiles("mgrs", tiles)
    print(f"{mission_key}: {len(rows)} granules over {len(by_month)} months, {len(tiles)} tiles imported", file=out)
    return {"granules": len(rows), "months": len(by_month), "tiles": len(tiles)}


_DROPPED = {"eco", "hls_near", "year"}


def import_footprints(cat, path, mission, now=months.now_iso, out=sys.stdout):
    """Copy a swath mission's footprint file into the catalog, dropping the baked pairing."""
    data = json.loads(Path(path).read_text())
    rows = []
    for feature in data.get("features", []):
        props = feature["properties"]
        ring = feature["geometry"]["coordinates"][0]
        minlon, minlat, maxlon, maxlat = spatial.ring_bbox(ring)
        row = {"id": props["id"], "start": props["start"], "end": props.get("end") or props["start"],
               "minlon": minlon, "minlat": minlat, "maxlon": maxlon, "maxlat": maxlat, "ring": ring,
               "browse": props.get("browse"), "data": props.get("data")}
        for name in mission.attributes:
            row[name] = props.get(name)
        rows.append(row)
    by_month = _months_of(rows)
    for month, month_rows in sorted(by_month.items()):
        cat.replace_month(mission.key, month, month_rows, now())
    print(f"{mission.key}: {len(rows)} granules over {len(by_month)} months imported", file=out)
    return {"granules": len(rows), "months": len(by_month)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import the old HLS store and footprint files into the catalog.")
    parser.add_argument("--registry", default=str(paths.MISSIONS))
    parser.add_argument("--catalog")
    parser.add_argument("--hls", default=str(paths.HLS_DB))
    parser.add_argument("--emit", default=str(paths.EMIT_FOOTPRINTS))
    parser.add_argument("--eco", default=str(paths.ECO_FOOTPRINTS))
    args = parser.parse_args(argv)

    reg = registry.load(args.registry)
    cat = catalog.Catalog(args.catalog or paths.catalog_db(reg.region.name))
    cat.bulk_mode()
    try:
        if Path(args.hls).is_file():
            import_hls(cat, args.hls, out=sys.stdout)
        else:
            print(f"migrate: no HLS store at {args.hls}; skipped", file=sys.stderr)
        for key, source in (("emit", args.emit), ("eco", args.eco)):
            if key in reg.missions and Path(source).is_file():
                import_footprints(cat, source, reg.mission(key), out=sys.stdout)
            else:
                print(f"migrate: no {key} footprints at {source}; skipped", file=sys.stderr)
        refresh.finish_grids(cat, reg)
        for key in cat.missions_present():
            summary = cat.summary(key)
            print(f"{key}: {summary['count']} granules over {summary['months']} months in {cat.path}")
    finally:
        cat.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
