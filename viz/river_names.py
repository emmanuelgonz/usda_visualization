"""Join Natural Earth river names onto HydroRIVERS reaches.

HydroRIVERS reaches carry no names and Natural Earth lines are generalised, so
a name is assigned in two stages. In the core stage, a reach whose vertices sit
within CORE_KM (median) of one reference line takes that line's name, unless its
downstream reach carries the same name as a core match at least MOUTH_ORDER_GAP
Strahler orders higher (a tributary mouth lying along the mainstem). In the
propagation stage, a named reach passes its name along the flow network to an
adjacent reach (downstream, or the dominant upstream reach) that continues the
same stream, provided that reach lies within LOOSE_KM (median) of the same
reference line. Continuation is judged by Strahler order: a stream keeps its
order or steps up by one where it meets an equal-order tributary, and at a
confluence of equals the reach with the larger discharge carries the name.

Natural Earth carries several rivers that share a name (two Colorados, two
Reds, a White in Arkansas and another in the Dakotas) and each feature is
joined separately, so a name can label two unrelated rivers and the tooltip
shows the name only. The CONUS bounding box used by the vendoring takes in
southern Ontario and Quebec, so a few Canadian reaches are named, including
Ontario's own Mississippi River.

The module is pure Python and NumPy. Reaches are dicts
{"next": id or None, "ord": int, "dis": float, "up": float, "parts": [[[lon, lat], ...], ...]}
keyed by HydroRIVERS id; a reference is a (name, [[lon, lat], ...]) tuple.
"""

import math
from collections import Counter, deque

import numpy as np

CORE_KM = 1.0        # a reach whose vertices sit within this median distance of one reference line takes its name
LOOSE_KM = 3.0       # propagation along the network may reach this far from the same reference line
STEP_KM = 0.5        # reference lines are densified to this spacing before indexing
MOUTH_ORDER_GAP = 2  # a core reach this many orders below its same-named core downstream reach is a tributary mouth, not the mainstem
CELL_DEG = 0.05      # grid cell for the reference-point index; a 3x3 block covers LOOSE_KM at every CONUS latitude

_KEY_OFFSET = 100000
_KEY_STRIDE = 1000000


def km_scale(lat):
    """(kx, ky): kilometres per degree of longitude and of latitude at lat."""
    return 111.0 * math.cos(math.radians(lat)), 111.0


def densify(line, step_km=STEP_KM):
    """The line's points plus interpolated points, no two consecutive more than step_km apart."""
    if not line:
        return []
    dense = [list(line[0])]
    for (x0, y0), (x1, y1) in zip(line, line[1:]):
        kx, ky = km_scale((y0 + y1) / 2.0)
        length = math.hypot((x1 - x0) * kx, (y1 - y0) * ky)
        pieces = max(1, math.ceil(length / step_km))
        for k in range(1, pieces):
            t = k / pieces
            dense.append([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t])
        dense.append([x1, y1])
    return dense


def _cell_keys(lons, lats):
    cx = np.floor(np.asarray(lons) / CELL_DEG).astype(np.int64)
    cy = np.floor(np.asarray(lats) / CELL_DEG).astype(np.int64)
    return cx, cy


def _key(cx, cy):
    return (cx + _KEY_OFFSET) * _KEY_STRIDE + (cy + _KEY_OFFSET)


class ReferenceIndex:
    """Densified reference points bucketed by CELL_DEG cell, queried over a 3x3 block of cells."""

    def __init__(self, references):
        self.names = []
        name_ids = {}
        lons, lats, ids = [], [], []
        for name, line in references:
            if name not in name_ids:
                name_ids[name] = len(self.names)
                self.names.append(name)
            points = densify(line)
            lons.extend(p[0] for p in points)
            lats.extend(p[1] for p in points)
            ids.extend([name_ids[name]] * len(points))
        self._lon = np.array(lons, dtype=np.float64)
        self._lat = np.array(lats, dtype=np.float64)
        self._name = np.array(ids, dtype=np.int64)
        self._cells = {}
        if len(self._lon):
            cx, cy = _cell_keys(self._lon, self._lat)
            keys = _key(cx, cy)
            order = np.argsort(keys, kind="stable")
            sorted_keys = keys[order]
            starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
            ends = np.r_[starts[1:], len(order)]
            for start, end in zip(starts, ends):
                self._cells[int(sorted_keys[start])] = order[start:end]
        self._blocks = {}

    def _block(self, cx, cy):
        """(lon, lat, name_id) arrays of every point in the 3x3 block around cell (cx, cy), or None."""
        key = (cx, cy)
        if key not in self._blocks:
            found = [self._cells[k] for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                     if (k := _key(cx + dx, cy + dy)) in self._cells]
            if found:
                take = np.concatenate(found)
                self._blocks[key] = (self._lon[take], self._lat[take], self._name[take])
            else:
                self._blocks[key] = None
        return self._blocks[key]

    def nearest_many(self, lons, lats, chunk=2000):
        """(distance_km, name_id) arrays; inf and -1 where the 3x3 block is empty."""
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        dist = np.full(len(lons), np.inf)
        name = np.full(len(lons), -1, dtype=np.int64)
        if not len(lons):
            return dist, name
        cx, cy = _cell_keys(lons, lats)
        keys = _key(cx, cy)
        order = np.argsort(keys, kind="stable")
        sorted_keys = keys[order]
        starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
        ends = np.r_[starts[1:], len(order)]
        for start, end in zip(starts, ends):
            members = order[start:end]
            block = self._block(int(cx[members[0]]), int(cy[members[0]]))
            if block is None:
                continue
            b_lon, b_lat, b_name = block
            for lo in range(0, len(members), chunk):
                sel = members[lo:lo + chunk]
                kx = 111.0 * np.cos(np.radians(lats[sel]))[:, None]
                dx = (lons[sel][:, None] - b_lon[None, :]) * kx
                dy = (lats[sel][:, None] - b_lat[None, :]) * 111.0
                d2 = dx * dx + dy * dy
                best = np.argmin(d2, axis=1)
                dist[sel] = np.sqrt(d2[np.arange(len(sel)), best])
                name[sel] = b_name[best]
        return dist, name

    def nearest(self, lon, lat):
        """(distance_km, name) of the nearest reference point in the 3x3 block, or (inf, None)."""
        dist, name = self.nearest_many([lon], [lat])
        if name[0] < 0:
            return math.inf, None
        return float(dist[0]), self.names[int(name[0])]


def reach_match(index, parts):
    """(median_km, name): the name nearest for the most vertices (ties to the smaller mean
    distance), and the median vertex distance to that named reference only. Vertices whose nearest
    reference has another name count as inf, so the median is finite only when more than half the
    vertices lie on the chosen name; (inf, None) when no vertex has a reference in reach."""
    lons = [v[0] for part in parts for v in part]
    lats = [v[1] for part in parts for v in part]
    dist, name = index.nearest_many(lons, lats)
    hit = name >= 0
    if not hit.any():
        return math.inf, None
    counts = Counter(name[hit].tolist())
    best = min(counts, key=lambda n: (-counts[n], float(dist[name == n].mean()), n))
    return float(np.median(np.where(name == best, dist, np.inf))), index.names[best]


def assign_names(reaches, references):
    """{reach_id: name}: core distance rule, then propagation along the flow network."""
    index = ReferenceIndex(references)
    matches = {rid: reach_match(index, r["parts"]) for rid, r in reaches.items()}
    ups = {}
    for rid in sorted(reaches):
        nxt = reaches[rid]["next"]
        if nxt is not None and nxt in reaches:
            ups.setdefault(nxt, []).append(rid)

    def is_mouth(rid):
        down = reaches[rid]["next"]
        return (down is not None and down in reaches
                and matches[down][1] == matches[rid][1] and matches[down][0] <= CORE_KM
                and reaches[down]["ord"] >= reaches[rid]["ord"] + MOUTH_ORDER_GAP)

    names = {rid: matches[rid][1] for rid in sorted(reaches)
             if matches[rid][0] <= CORE_KM and not is_mouth(rid)}
    queue = deque(sorted(names))
    while queue:
        rid = queue.popleft()
        order, name = reaches[rid]["ord"], names[rid]
        candidates = []
        down = reaches[rid]["next"]
        if down is not None and down in reaches:
            d_ord = reaches[down]["ord"]
            if d_ord == order or (d_ord == order + 1
                                  and max(reaches[u]["ord"] for u in ups[down]) == order):
                candidates.append(down)
        upstream = ups.get(rid)
        if upstream:
            top = max(upstream, key=lambda u: (reaches[u]["ord"], reaches[u]["dis"]))
            t_ord = reaches[top]["ord"]
            equals = sum(1 for u in upstream if reaches[u]["ord"] == order - 1)
            if t_ord == order or (t_ord == order - 1 and equals >= 2):
                candidates.append(top)
        for cand in candidates:
            median, matched = matches[cand]
            if cand not in names and matched == name and median <= LOOSE_KM:
                names[cand] = name
                queue.append(cand)
    return names
