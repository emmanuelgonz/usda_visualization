"""Swath missions: one JSON query per collection-month, one ring per scene.

JSON is needed because only the JSON endpoint carries the polygon or box.
Coverage against each tiled grid is computed once per granule so that
coincidence with a tiled partner is a join on tile and date.
"""

import re

from viz import cmr, spatial
from viz.months import month_bounds


def page_url(collection, bbox, month, page_num):
    start, end = month_bounds(month)
    box = ",".join(str(v) for v in bbox)
    return (f"{cmr.CMR_URL}?short_name={collection.short_name}&version={collection.version}&bounding_box={box}"
            f"&temporal={start}T00:00:00Z,{end}T00:00:00Z&page_size={cmr.PAGE_SIZE}&page_num={page_num}")


def _box_ring(box):
    south, west, north, east = (float(v) for v in box.split())
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def _ring(mission, entry):
    if mission.footprint == "box":
        boxes = entry.get("boxes")
        return _box_ring(boxes[0]) if boxes else None
    ring = cmr.polygon_ring(entry)
    if ring is None or len({tuple(p) for p in ring}) < 3:
        return None
    return ring


def _derived(entry):
    domains = entry.get("orbit_calculated_spatial_domains") or []
    orbit = domains[0].get("start_orbit_number") if domains else None
    return {"orbit": orbit}


def _attribute(spec, entry, collection, derived):
    if spec.source in collection.implies:
        value = collection.implies[spec.source]
    elif spec.source in derived:
        value = derived[spec.source]
    else:
        value = entry.get(spec.source)
    if value in (None, ""):
        return None
    if spec.type == "number":
        return float(value)
    if spec.type == "int":
        return int(float(value))
    return value


def _link(entry, rel_suffix, match):
    pattern = re.compile(match)
    for item in entry.get("links", []):
        href = item.get("href", "")
        if item.get("rel", "").endswith(rel_suffix) and href.startswith("http") and pattern.search(href):
            return href
    return None


def entry_to_row(mission, collection, entry):
    """A catalog row from one CMR entry, or None without a start time or a usable footprint."""
    start = entry.get("time_start")
    ring = _ring(mission, entry)
    if not start or ring is None:
        return None
    minlon, minlat, maxlon, maxlat = spatial.ring_bbox(ring)
    row = {"id": entry.get("title"), "start": start, "end": entry.get("time_end") or start,
           "minlon": minlon, "minlat": minlat, "maxlon": maxlon, "maxlat": maxlat, "ring": ring,
           "browse": _link(entry, "browse#", mission.browse["match"]) if mission.browse.get("source") == "links" else None,
           "data": None}
    derived = _derived(entry)
    for name, spec in mission.attributes.items():
        row[name] = _attribute(spec, entry, collection, derived)
    for name, spec in mission.links.items():
        row[name] = _link(entry, spec["rel"], spec["match"])
    return row


def fetch_month(mission, collection, bbox, month, fetch_fn=cmr.fetch_page):
    """Every row of one collection-month, paging until an empty page, limited to the month's dates."""
    rows = []
    page = 1
    while True:
        entries = fetch_fn(page_url(collection, bbox, month, page))
        if not entries:
            break
        rows.extend(r for r in (entry_to_row(mission, collection, e) for e in entries) if r is not None)
        page += 1
    start, end = month_bounds(month)
    return [r for r in rows if start <= r["start"][:10] < end]


def compute_coverage(cat, mission, grid):
    """Write coverage rows for the mission's granules not yet covered against the grid; returns rows written."""
    tiles = cat.tile_rings(grid)
    if not tiles:
        return 0
    index = spatial.RingIndex((ring, (tile, ring)) for tile, ring in tiles)
    rows = []
    for gid, box, ring in cat.uncovered(mission.key, grid):
        for tile, tile_ring in index.intersecting(box):
            if spatial.rings_intersect(ring, tile_ring):
                rows.append((mission.key, gid, grid, tile))
    cat.put_coverage(rows)
    return len(rows)
