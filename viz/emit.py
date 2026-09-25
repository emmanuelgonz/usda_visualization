"""EMIT footprints: the CPC week-to-date rule, containment, and a point index.

The index is built lazily from data/emit/footprints.geojson and kept at
module level, keyed by the file's path and modification time, so a refetch
is picked up without a restart and tests can point it at a fixture.
"""

import datetime
import json
import math
import os
import re
import threading
from pathlib import Path


def week_sunday(year, week):
    """The Sunday ending CPC week N of a year.

    CPC week numbers are ISO week numbers: the report for the week ending
    Sunday 2024-04-14 (ISO week 15) was published Monday 2024-04-15, which
    is the timestamp on cornCond24w15.tif in the archive.
    """
    return datetime.date.fromisocalendar(year, week, 7)


def point_in_ring(lon, lat, ring):
    """Ray-casting containment for a lon/lat ring, closed or not."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        crosses = (yi > lat) != (yj > lat)
        if crosses and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


_ORBIT_RE = re.compile(r"_(\d{7})_(\d{3})$")


def orbit_key(scene_id):
    """('2421214', 4) from '…_2421214_004'; None when the ID lacks the orbit fields."""
    match = _ORBIT_RE.search(scene_id or "")
    return (match.group(1), int(match.group(2))) if match else None


def ring_centroid(ring):
    """Mean of the ring's vertices, ignoring a closing duplicate."""
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


class FootprintIndex:
    """All footprints from one GeoJSON file, queryable by point."""

    def __init__(self, path):
        path = Path(path)
        data = json.loads(path.read_text())
        self._items = []
        self._by_id = {}
        self._by_orbit = {}
        for feature in data.get("features", []):
            ring = feature["geometry"]["coordinates"][0]
            lons = [p[0] for p in ring]
            lats = [p[1] for p in ring]
            bbox = (min(lons), min(lats), max(lons), max(lats))
            self._items.append((bbox, ring, feature["properties"]))
            scene_id = feature["properties"].get("id")
            self._by_id[scene_id] = (bbox, ring, feature["properties"])
            key = orbit_key(scene_id)
            if key:
                self._by_orbit[key] = feature["properties"]
        self.count = len(self._items)
        self.fetched = datetime.datetime.fromtimestamp(
            path.stat().st_mtime, tz=datetime.timezone.utc
        ).isoformat(timespec="seconds")

    def scene(self, scene_id):
        """Properties of one footprint by granule ID, or None."""
        item = self._by_id.get(scene_id)
        return item[2] if item else None

    def scene_bbox(self, scene_id):
        """(minlon, minlat, maxlon, maxlat) of one footprint, or None."""
        item = self._by_id.get(scene_id)
        return item[0] if item else None

    def scene_ring(self, scene_id):
        """The footprint ring's coordinates for one granule ID, or None."""
        item = self._by_id.get(scene_id)
        return item[1] if item else None

    def neighbour(self, scene_id):
        """(properties, sign) of the next (+1) or else the previous (-1) scene in the orbit, or None."""
        key = orbit_key(scene_id)
        if not key:
            return None
        orbit, number = key
        for other, sign in ((number + 1, 1), (number - 1, -1)):
            props = self._by_orbit.get((orbit, other))
            if props is not None:
                return props, sign
        return None

    def covering(self, lon, lat):
        """Properties of every footprint containing the point, newest first."""
        hits = []
        for (minx, miny, maxx, maxy), ring, props in self._items:
            if minx <= lon <= maxx and miny <= lat <= maxy and point_in_ring(lon, lat, ring):
                hits.append(props)
        hits.sort(key=lambda p: p.get("start") or "", reverse=True)
        return hits

    def count_where(self, predicate):
        """Number of footprints whose properties satisfy the predicate."""
        return sum(1 for _, _, props in self._items if predicate(props))


def scene_corners(index, scene_id):
    """The scene's ring vertices in image order [top-left, top-right, bottom-right, bottom-left].

    Rows of the browse image increase along the flight direction, which is
    the vector from this scene's centroid to the next scene's in the same
    orbit (or from the previous scene's to this one). The two vertices behind
    the centroid form the top edge; columns increase toward the left of the
    flight direction, so the top-left corner is the vertex on the right.
    None when the scene is unknown or has no orbit neighbour.
    """
    ring = index.scene_ring(scene_id)
    found = index.neighbour(scene_id)
    if ring is None or found is None:
        return None
    other, sign = found
    here = ring_centroid(ring)
    there = ring_centroid(index.scene_ring(other["id"]))
    scale = math.cos(math.radians(here[1]))
    dx = (there[0] - here[0]) * scale * sign
    dy = (there[1] - here[1]) * sign
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    dx, dy = dx / length, dy / length
    placed = []
    for lon, lat in ring[:4]:
        vx = (lon - here[0]) * scale
        vy = lat - here[1]
        placed.append((vx * dx + vy * dy, dx * vy - dy * vx, [lon, lat]))   # (along, left, vertex)
    placed.sort(key=lambda item: item[0])
    top, bottom = placed[:2], placed[2:]
    top_left = min(top, key=lambda item: item[1])[2]
    top_right = max(top, key=lambda item: item[1])[2]
    bottom_left = min(bottom, key=lambda item: item[1])[2]
    bottom_right = max(bottom, key=lambda item: item[1])[2]
    return [top_left, top_right, bottom_right, bottom_left]


_lock = threading.Lock()
_cache = {}


def index_for(path):
    """The FootprintIndex for a path, rebuilt when the file changes; None if absent."""
    path = Path(path)
    if not path.is_file():
        return None
    key = str(path)
    stamp = os.stat(path).st_mtime_ns
    with _lock:
        cached = _cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    index = FootprintIndex(path)
    with _lock:
        _cache[key] = (stamp, index)
    return index
