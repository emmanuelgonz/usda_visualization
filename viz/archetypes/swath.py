"""Swath missions: one JSON query per collection-month, one ring per scene.

JSON is needed because only the JSON endpoint carries the polygon or box.
Coverage against each tiled grid is computed once per granule so that
coincidence with a tiled partner is a join on tile and date.
"""

import json
import re

from viz import cmr, spatial
from viz.months import month_bounds

COVERAGE_FLUSH = 50000


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


def fetch_month(mission, collection, bbox, month, fetch_fn=None):
    """Every row of one collection-month, paged by CMR-Hits, limited to the month's dates.

    Raises ValueError without a usable CMR-Hits header, or when the pages
    deliver fewer entries than CMR-Hits promised, so a short page is never
    recorded as a completed fetch and frozen.
    """
    fetch_fn = fetch_fn or cmr.fetch_response
    url = page_url(collection, bbox, month, 1)
    body, headers = fetch_fn(url)
    try:
        hits = int(headers.get("CMR-Hits"))
    except (TypeError, ValueError):
        raise ValueError(f"no CMR-Hits header in the response for {url}") from None
    entries = json.loads(body)["feed"]["entry"]
    pages = -(-hits // cmr.PAGE_SIZE)
    for page in range(2, pages + 1):
        body, _ = fetch_fn(page_url(collection, bbox, month, page))
        entries.extend(json.loads(body)["feed"]["entry"])
    if len(entries) < hits:
        raise ValueError(f"{collection.short_name} {month}: CMR reported {hits} granules but delivered {len(entries)}")
    rows = [r for r in (entry_to_row(mission, collection, e) for e in entries) if r is not None]
    start, end = month_bounds(month)
    return [r for r in rows if start <= r["start"][:10] < end]


def compute_coverage(cat, mission, grid):
    """Write coverage rows for the mission's granules not yet covered against the grid; returns rows written."""
    tiles = cat.tile_rings(grid)
    if not tiles:
        return 0
    index = spatial.RingIndex((ring, (tile, ring)) for tile, ring in tiles)
    rows = []
    written = 0
    for gid, box, ring in cat.uncovered(mission.key, grid):
        for tile, tile_ring in index.intersecting(box):
            if spatial.rings_intersect(ring, tile_ring):
                rows.append((mission.key, gid, grid, tile))
        if len(rows) >= COVERAGE_FLUSH:
            cat.put_coverage(rows)
            written += len(rows)
            rows = []
    cat.put_coverage(rows)
    return written + len(rows)
