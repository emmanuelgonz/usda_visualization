"""Point, box, and ring tests in lon/lat, and a cell-bucketed ring index.

Used by both archetypes (which scenes cover a point, which tiles a scene
touches) and by the basins. Everything is plain Python on lists of
[lon, lat] pairs; rings may be closed or open.
"""

import math
from collections import defaultdict


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


def ring_bbox(ring):
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lons), min(lats), max(lons), max(lats))


def boxes_intersect(a, b):
    """Closed-interval overlap of two (minlon, minlat, maxlon, maxlat) boxes."""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _orient(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _segments_cross(p1, p2, q1, q2):
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


def _edges(ring):
    pts = ring if ring[0] == ring[-1] else ring + [ring[0]]
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def rings_intersect(a, b):
    """True when the rings share area: a vertex of one inside the other, or two edges crossing.

    Rings that touch only along an edge or at a corner count as intersecting because boundary
    vertices test as inside under the half-open ray cast; this is accepted for coverage since
    real scene and tile coordinates never align exactly and a spurious coverage row is harmless.
    """
    if not boxes_intersect(ring_bbox(a), ring_bbox(b)):
        return False
    if any(point_in_ring(x, y, b) for x, y in a) or any(point_in_ring(x, y, a) for x, y in b):
        return True
    return any(_segments_cross(p1, p2, q1, q2) for p1, p2 in _edges(a) for q1, q2 in _edges(b))


class RingIndex:
    """Rings bucketed into cells of `cell` degrees; each ring sits in every cell its bbox touches."""

    def __init__(self, items, cell=1.0):
        self.cell = cell
        self._items = []
        self._cells = defaultdict(list)
        for ring, payload in items:
            bbox = ring_bbox(ring)
            index = len(self._items)
            self._items.append((bbox, ring, payload))
            for cx in range(math.floor(bbox[0] / cell), math.floor(bbox[2] / cell) + 1):
                for cy in range(math.floor(bbox[1] / cell), math.floor(bbox[3] / cell) + 1):
                    self._cells[(cx, cy)].append(index)
        self.count = len(self._items)

    def _ordered(self, box):
        """Distinct item ids whose cells the box touches, in insertion order."""
        seen = set()
        for cx in range(math.floor(box[0] / self.cell), math.floor(box[2] / self.cell) + 1):
            for cy in range(math.floor(box[1] / self.cell), math.floor(box[3] / self.cell) + 1):
                seen.update(self._cells.get((cx, cy), ()))
        return sorted(seen)

    def covering(self, lon, lat):
        """Payloads of every ring containing the point, in insertion order."""
        out = []
        for index in self._ordered((lon, lat, lon, lat)):
            (minx, miny, maxx, maxy), ring, payload = self._items[index]
            if minx <= lon <= maxx and miny <= lat <= maxy and point_in_ring(lon, lat, ring):
                out.append(payload)
        return out

    def intersecting(self, box):
        """Payloads of every ring whose bbox meets the box, in insertion order (bbox test only)."""
        return [self._items[i][2] for i in self._ordered(box) if boxes_intersect(self._items[i][0], box)]
