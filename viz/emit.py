"""EMIT footprints: the CPC week-to-date rule, containment, and a point index.

The index is built lazily from data/emit/footprints.geojson and kept at
module level, keyed by the file's path and modification time, so a refetch
is picked up without a restart and tests can point it at a fixture.
"""

import datetime
import json
import os
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


class FootprintIndex:
    """All footprints from one GeoJSON file, queryable by point."""

    def __init__(self, path):
        path = Path(path)
        data = json.loads(path.read_text())
        self._items = []
        for feature in data.get("features", []):
            ring = feature["geometry"]["coordinates"][0]
            lons = [p[0] for p in ring]
            lats = [p[1] for p in ring]
            bbox = (min(lons), min(lats), max(lons), max(lats))
            self._items.append((bbox, ring, feature["properties"]))
        self.count = len(self._items)
        self.fetched = datetime.datetime.fromtimestamp(
            path.stat().st_mtime, tz=datetime.timezone.utc
        ).isoformat(timespec="seconds")

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
