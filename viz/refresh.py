"""Refresh the region catalog from CMR for every mission in the registry.

Run by ./run.sh refresh [MISSION ...]. For each mission and month since its
registry `since`, a frozen month is skipped, the mission's collections are
paged, and the month is replaced in one transaction. Then new tile ids get
outlines and swath granules get coverage rows against every grid in use.
"""

import argparse
import sys

from viz import archetypes, catalog, cmr, grids, months, paths, registry


def refresh_mission(cat, reg, mission, first=None, fetch_csv=None, fetch_json=None,
                    out=None, now=months.now_iso):
    """Fetch the mission's non-frozen months into the catalog; returns counts.

    The fetchers default to the cmr module's functions at call time, so a test
    can patch viz.cmr.fetch_response.
    """
    out = out or sys.stdout
    fetch_csv = fetch_csv or cmr.fetch_response
    fetch_json = fetch_json or cmr.fetch_response
    archetype = archetypes.get(mission.archetype)
    counts = {"months": 0, "granules": 0}
    for month in months.months_between(first or mission.since, months.current_month()):
        fetched = cat.fetched_at(mission.key, month)
        if fetched and months.is_frozen(month, fetched):
            continue
        rows = []
        for collection in mission.cmr:
            if mission.archetype == "tiled":
                rows.extend(archetype.fetch_month(mission, collection, reg.region.bbox, month, fetch_fn=fetch_csv))
            else:
                rows.extend(archetype.fetch_month(mission, collection, reg.region.bbox, month, fetch_fn=fetch_json))
        cat.replace_month(mission.key, month, rows, now())
        counts["months"] += 1
        counts["granules"] += len(rows)
        print(f"{mission.key} {month}: {len(rows)} granules", file=out, flush=True)
    return counts


def finish_grids(cat, reg, out=None):
    """Outlines for new tile ids of every tiled mission, then coverage for every swath mission."""
    out = out or sys.stdout
    in_use = []
    for mission in reg.tiled():
        grid = grids.get(mission.grid)
        known = cat.known_tiles(mission.grid)
        outlines = []
        for tile in cat.distinct_tiles(mission.key):
            if tile in known:
                continue
            ring = grid.ring(tile)
            if ring is None:
                print(f"{mission.key}: cannot place tile {tile}; it is counted but not drawn", file=sys.stderr)
                continue
            outlines.append((tile, ring))
        cat.put_tiles(mission.grid, outlines)
        print(f"{mission.grid}: {len(outlines)} tile outlines computed", file=out, flush=True)
        if mission.grid not in in_use:
            in_use.append(mission.grid)
    swath = archetypes.get("swath")
    for mission in reg.swath():
        for grid_name in in_use:
            written = swath.compute_coverage(cat, mission, grid_name)
            print(f"{mission.key} coverage: {written} rows against {grid_name}", file=out, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh the region catalog from CMR.")
    parser.add_argument("missions", nargs="*", help="mission keys (default: every mission in the registry)")
    parser.add_argument("--from", dest="first", help="first month to fetch, YYYY-MM (default: each mission's since)")
    parser.add_argument("--registry", default=str(paths.MISSIONS))
    parser.add_argument("--catalog", help="catalog path (default: data/catalog/<region>.sqlite)")
    args = parser.parse_args(argv)

    reg = registry.load(args.registry)
    unknown = [k for k in args.missions if k not in reg.missions]
    if unknown:
        print(f"unknown mission(s): {', '.join(unknown)}; known: {', '.join(reg.missions)}", file=sys.stderr)
        return 2
    selected = [reg.mission(k) for k in args.missions] or list(reg.missions.values())
    cat = catalog.Catalog(args.catalog or paths.catalog_db(reg.region.name))
    cat.bulk_mode()
    try:
        for mission in selected:
            counts = refresh_mission(cat, reg, mission, first=args.first)
            print(f"{mission.key}: {counts['months']} months, {counts['granules']} granules", flush=True)
        finish_grids(cat, reg)
        cat.analyze()
        for mission in selected:
            summary = cat.summary(mission.key)
            print(f"{mission.key}: {summary['count']} granules over {summary['months']} months in {cat.path}")
    finally:
        cat.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
