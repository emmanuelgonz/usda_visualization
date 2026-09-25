"""Hydrologic-unit lookup: which HUC2 region and HUC4 subregion contain a point.

The two vendored GeoJSON files (viz/web/vendor/basins2.geojson and
basins4.geojson) hold MultiPolygons with holes, so containment tests the
outer ring of each polygon and then every hole, unlike the single-ring EMIT
footprints. Indexes are cached per path and rebuilt when the file changes.
"""

import json
import os
import threading
from pathlib import Path

from viz.emit import point_in_ring


def _polygons(geometry):
    """List of polygons, each a list of rings (outer first), from a GeoJSON geometry."""
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return list(geometry["coordinates"])
    return []


def point_in_polygon(lon, lat, rings):
    """Inside the outer ring and outside every hole."""
    if not rings or not point_in_ring(lon, lat, rings[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in rings[1:])


class BasinIndex:
    """All units from one GeoJSON file, queryable by point."""

    def __init__(self, path):
        data = json.loads(Path(path).read_text())
        self._items = []
        for feature in data.get("features", []):
            for rings in _polygons(feature["geometry"]):
                lons = [p[0] for p in rings[0]]
                lats = [p[1] for p in rings[0]]
                bbox = (min(lons), min(lats), max(lons), max(lats))
                self._items.append((bbox, rings, feature["properties"]))
        self.count = len(data.get("features", []))

    def covering(self, lon, lat):
        """{huc, name} of the first unit containing the point, or None."""
        for (minx, miny, maxx, maxy), rings, props in self._items:
            if minx <= lon <= maxx and miny <= lat <= maxy and point_in_polygon(lon, lat, rings):
                return {"huc": props["huc"], "name": props["name"]}
        return None


_lock = threading.Lock()
_cache = {}


def index_for(path):
    """The BasinIndex for a path, rebuilt when the file changes; None if absent."""
    path = Path(path)
    if not path.is_file():
        return None
    key = str(path)
    stamp = os.stat(path).st_mtime_ns
    with _lock:
        cached = _cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    index = BasinIndex(path)
    with _lock:
        _cache[key] = (stamp, index)
    return index
