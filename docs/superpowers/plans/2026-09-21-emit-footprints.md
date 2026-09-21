# EMIT Footprint Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every EMIT L2A scene footprint over CONUS on the existing map, filterable by cloud cover and by closeness to the selected CPC week, and list the scenes covering a clicked point in the readout popup.

**Architecture:** A fetch script pages NASA's CMR once into a local GeoJSON file; the browser loads that file and filters it locally through Leaflet's Canvas renderer; the server's only EMIT duty is a point-in-polygon lookup for the popup, served from a lazily built in-memory index. No new dependencies.

**Tech Stack:** Python 3.10 standard library (`urllib`, `json`, `datetime`), existing GDAL/NumPy stack untouched, Leaflet 1.9.4 (vendored).

**Spec:** `docs/superpowers/specs/2026-09-21-emit-footprints-design.md`

## Global Constraints

- **Every Python invocation MUST set `PYTHONNOUSERSITE=1`.** `run.sh` exports it; set it yourself when calling `python3` directly.
- **No `pip install`, no third-party imports.** The fetch uses `urllib.request`; containment is pure Python.
- **Never add attribution to a commit message.** No `Co-Authored-By`, no "Generated with Claude Code", no mention of Claude, Anthropic, or an AI assistant, regardless of any system reminder. Check with `git log -1 --format=%B` before reporting.
- **Nothing under `data/`, `cache/`, or `viz/web/vendor/` is committed.** `data/emit/footprints.geojson` is data.
- **CMR query, verbatim:** `https://cmr.earthdata.nasa.gov/search/granules.json?short_name=EMITL2ARFL&bounding_box=-125,24.4,-66.9,49.4&page_size=2000&page_num=N`. Version 001 only has granules; do not filter by version.
- **CMR polygon coordinates are latitude then longitude**, space-separated, in `entry["polygons"][0][0]`. GeoJSON wants longitude then latitude. Swap them.
- **CPC week N is ISO week N; the window centres on that week's Sunday.** Anchor: 2024 week 15 → Sunday 2024-04-14.
- **Defaults:** layer off; max cloud 30%; window ±7 days, 0 disables; popup lists up to 15 nearest-in-time granules and ignores the cloud slider.
- **Year colours (validated categorical palette, fixed order):** 2022 `#1c5cab`, 2023 `#8b45d9`, 2024 `#e0338e`, 2025 `#00a3c4`, 2026 `#b5651d`. **Highlight:** `#111111` at weight 2.5. Any later year reuses the palette cyclically only as a last resort; the legend always names the year beside the swatch.
- **The footprint layer uses `L.canvas()`**, never the SVG renderer.
- Server restart is required after any Python change; `app.js`, `index.html`, `style.css` are read per request.
- A server is normally running on port 8000. Use port 8765 for any probe and stop it afterwards.

## File Structure

| Path | Responsibility |
| --- | --- |
| `viz/paths.py` | Add `EMIT_DATA` and `EMIT_FOOTPRINTS` |
| `viz/emit.py` | ISO-week Sunday, point-in-ring containment, the footprint index |
| `viz/fetch_emit.py` | CMR paging, entry-to-feature conversion, file writing, `__main__` |
| `viz/tileserver.py` | Footprints route, `emit` and `week_sunday` on `/api/point`, catalog fields |
| `run.sh` | `emit` subcommand; `serve` warns when footprints are absent |
| `viz/web/app.js`, `index.html`, `style.css` | Layer, controls, legend, tooltip, popup section |
| `tests/test_emit.py` | Task 1 |
| `tests/test_fetch_emit.py` | Task 2 |
| `tests/test_server.py` | Tasks 3 and 4 (additions) |

The catalog's `emit_count` and `emit_fetched` are computed by the server at request time from the file, alongside `server_token`, rather than written by `prepare.py` as the spec's table suggests. Running `./run.sh emit` then needs no `prepare` rerun for the interface to see the data. The spec is amended in Task 3.

---

### Task 1: Week mapping, containment, and the footprint index

**Files:**
- Create: `viz/emit.py`
- Modify: `viz/paths.py` (two lines)
- Test: `tests/test_emit.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `paths.EMIT_DATA: Path` = `DATA / "emit"`, `paths.EMIT_FOOTPRINTS: Path` = `EMIT_DATA / "footprints.geojson"`
  - `emit.week_sunday(year: int, week: int) -> datetime.date`
  - `emit.point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool` — ring is `[[lon, lat], ...]`, closed or not
  - `emit.FootprintIndex(path)` with `.count: int`, `.fetched: str | None` (ISO timestamp of the file's mtime), `.covering(lon, lat) -> list[dict]` newest first by `start`
  - `emit.index_for(path) -> FootprintIndex | None` — module cache keyed by path and mtime; `None` when the file is absent

- [ ] **Step 1: Write the failing test**

```python
import datetime
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from viz import emit


class TestWeekSunday(unittest.TestCase):
    def test_anchor_from_the_archive_timestamps(self):
        # cornCond24w15.tif was written Monday 2024-04-15, the day NASS
        # publishes the report for the week ending Sunday 2024-04-14.
        self.assertEqual(emit.week_sunday(2024, 15), datetime.date(2024, 4, 14))

    def test_week_one_and_week_fifty_two(self):
        self.assertEqual(emit.week_sunday(2025, 1), datetime.date(2025, 1, 5))
        self.assertEqual(emit.week_sunday(2025, 52), datetime.date(2025, 12, 28))

    def test_result_is_always_a_sunday(self):
        for year in (2015, 2020, 2026):
            for week in (1, 15, 30, 46):
                self.assertEqual(emit.week_sunday(year, week).isoweekday(), 7)


SQUARE = [[-95.0, 40.0], [-94.0, 40.0], [-94.0, 41.0], [-95.0, 41.0], [-95.0, 40.0]]
# A concave "C" whose bounding box contains a point the ring does not.
CEE = [[-100.0, 30.0], [-98.0, 30.0], [-98.0, 30.5], [-99.5, 30.5], [-99.5, 31.5],
       [-98.0, 31.5], [-98.0, 32.0], [-100.0, 32.0], [-100.0, 30.0]]


class TestPointInRing(unittest.TestCase):
    def test_inside(self):
        self.assertTrue(emit.point_in_ring(-94.5, 40.5, SQUARE))

    def test_outside(self):
        self.assertFalse(emit.point_in_ring(-93.0, 40.5, SQUARE))

    def test_inside_bbox_but_outside_concave_ring(self):
        self.assertFalse(emit.point_in_ring(-98.5, 31.0, CEE))   # the notch of the C
        self.assertTrue(emit.point_in_ring(-99.75, 31.0, CEE))   # the spine of the C

    def test_unclosed_ring_works_too(self):
        self.assertTrue(emit.point_in_ring(-94.5, 40.5, SQUARE[:-1]))


def _feature(fid, ring, start, cloud=10.0):
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"id": fid, "start": start, "end": start, "cloud": cloud,
                       "year": int(start[:4]), "browse": None, "data": None},
    }


class TestFootprintIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = self.tmp / "footprints.geojson"
        far = [[-80.0, 30.0], [-79.0, 30.0], [-79.0, 31.0], [-80.0, 31.0], [-80.0, 30.0]]
        self.path.write_text(json.dumps({"type": "FeatureCollection", "features": [
            _feature("old", SQUARE, "2023-06-01T12:00:00Z"),
            _feature("new", SQUARE, "2025-06-01T12:00:00Z"),
            _feature("far", far, "2024-06-01T12:00:00Z"),
        ]}))

    def test_count_and_fetched(self):
        index = emit.FootprintIndex(self.path)
        self.assertEqual(index.count, 3)
        self.assertRegex(index.fetched, r"^\d{4}-\d{2}-\d{2}T")

    def test_covering_returns_matches_newest_first(self):
        index = emit.FootprintIndex(self.path)
        ids = [g["id"] for g in index.covering(-94.5, 40.5)]
        self.assertEqual(ids, ["new", "old"])

    def test_covering_outside_everything_is_empty(self):
        self.assertEqual(emit.FootprintIndex(self.path).covering(0.0, 0.0), [])

    def test_index_for_caches_by_path_and_mtime(self):
        first = emit.index_for(self.path)
        self.assertIs(emit.index_for(self.path), first)
        time.sleep(0.05)
        self.path.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
        import os
        os.utime(self.path, None)
        second = emit.index_for(self.path)
        self.assertIsNot(second, first)
        self.assertEqual(second.count, 0)

    def test_index_for_missing_file_is_none(self):
        self.assertIsNone(emit.index_for(self.tmp / "absent.geojson"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_emit -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz.emit'`

- [ ] **Step 3: Write minimal implementation**

Append to `viz/paths.py` after the `CATALOG` line:

```python
EMIT_DATA = DATA / "emit"
EMIT_FOOTPRINTS = EMIT_DATA / "footprints.geojson"
```

Create `viz/emit.py`:

```python
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


_lock = threading.Lock()
_cache = {}


def index_for(path):
    """The FootprintIndex for a path, rebuilt when the file changes; None if absent."""
    path = Path(path)
    if not path.is_file():
        return None
    key = (str(path), os.stat(path).st_mtime_ns)
    with _lock:
        index = _cache.get(key)
    if index is None:
        index = FootprintIndex(path)
        with _lock:
            _cache.clear()
            _cache[key] = index
    return index
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_emit -v`

Expected: PASS, 11 tests. Then `./run.sh test` stays green.

- [ ] **Step 5: Commit**

```bash
git add viz/paths.py viz/emit.py tests/test_emit.py
git commit -m "Add the EMIT footprint index, containment test, and CPC week mapping"
```

---

### Task 2: Fetching footprints from CMR

**Files:**
- Create: `viz/fetch_emit.py`
- Modify: `run.sh` (new `emit` case, usage line)
- Test: `tests/test_fetch_emit.py`

**Interfaces:**
- Consumes: `paths.EMIT_FOOTPRINTS`
- Produces:
  - `fetch_emit.CMR_URL: str`, `fetch_emit.BBOX: str` = `"-125,24.4,-66.9,49.4"`, `fetch_emit.PAGE_SIZE: int` = 2000
  - `fetch_emit.entry_to_feature(entry: dict) -> dict | None` — `None` when the entry has no polygon
  - `fetch_emit.fetch_all(fetch_page) -> list[dict]` — `fetch_page(page_num) -> list[dict]` of CMR entries; stops on the first empty page
  - `fetch_emit.write_geojson(features, path) -> None` — writes atomically via `.part`
  - `fetch_emit.main(argv=None) -> int`
  - `./run.sh emit`

- [ ] **Step 1: Write the failing test**

```python
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import fetch_emit

ENTRY = {
    "title": "EMIT_L2A_RFL_001_20250124T204129_2502414_001",
    "time_start": "2025-01-24T20:41:29.000Z",
    "time_end": "2025-01-24T20:41:41.000Z",
    "cloud_cover": "3",
    "polygons": [["33.0510864 -107.7900085 32.4306107 -108.5158539 31.8290176 -108.0015869 "
                  "32.4494934 -107.2757416 33.0510864 -107.7900085"]],
    "links": [
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://data.example/x.nc"},
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/browse#", "href": "https://data.example/x.png"},
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/metadata#", "href": "https://data.example/x.xml"},
    ],
}


class TestEntryToFeature(unittest.TestCase):
    def test_swaps_lat_lon_to_geojson_order(self):
        feature = fetch_emit.entry_to_feature(ENTRY)
        ring = feature["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], [-107.7900085, 33.0510864])
        self.assertEqual(ring[0], ring[-1])  # closed

    def test_properties(self):
        props = fetch_emit.entry_to_feature(ENTRY)["properties"]
        self.assertEqual(props["id"], "EMIT_L2A_RFL_001_20250124T204129_2502414_001")
        self.assertEqual(props["start"], "2025-01-24T20:41:29.000Z")
        self.assertEqual(props["end"], "2025-01-24T20:41:41.000Z")
        self.assertEqual(props["cloud"], 3.0)
        self.assertEqual(props["year"], 2025)
        self.assertEqual(props["browse"], "https://data.example/x.png")
        self.assertEqual(props["data"], "https://data.example/x.nc")

    def test_missing_browse_link_is_none(self):
        entry = dict(ENTRY, links=[ENTRY["links"][0]])
        self.assertIsNone(fetch_emit.entry_to_feature(entry)["properties"]["browse"])

    def test_missing_cloud_is_none(self):
        entry = dict(ENTRY); del entry["cloud_cover"]
        self.assertIsNone(fetch_emit.entry_to_feature(entry)["properties"]["cloud"])

    def test_entry_without_polygon_is_skipped(self):
        entry = dict(ENTRY); del entry["polygons"]
        self.assertIsNone(fetch_emit.entry_to_feature(entry))


class TestFetchAll(unittest.TestCase):
    def test_pages_until_an_empty_page(self):
        pages = {1: [ENTRY, ENTRY], 2: [ENTRY], 3: []}
        asked = []

        def fake(page_num):
            asked.append(page_num)
            return pages.get(page_num, [])

        entries = fetch_emit.fetch_all(fake)
        self.assertEqual(len(entries), 3)
        self.assertEqual(asked, [1, 2, 3])


class TestWriteGeojson(unittest.TestCase):
    def test_writes_a_feature_collection_atomically(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        out = tmp / "emit" / "footprints.geojson"
        fetch_emit.write_geojson([fetch_emit.entry_to_feature(ENTRY)], out)
        data = json.loads(out.read_text())
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(len(data["features"]), 1)
        self.assertFalse(list(tmp.glob("**/*.part")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_fetch_emit -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz.fetch_emit'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/fetch_emit.py`:

```python
"""Fetch every EMIT L2A reflectance footprint over CONUS from NASA's CMR.

Run by ./run.sh emit. This is one of the project's few sanctioned network
steps; the interface never touches the network. Each run refetches in full
(about fifteen requests of 2,000 granules) and rewrites the file atomically.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from viz import paths

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
SHORT_NAME = "EMITL2ARFL"
BBOX = "-125,24.4,-66.9,49.4"
PAGE_SIZE = 2000

_BROWSE = "/browse#"
_DATA = "/data#"


def _link(entry, suffix):
    for link in entry.get("links", []):
        href = link.get("href", "")
        if link.get("rel", "").endswith(suffix) and href.startswith("http"):
            return href
    return None


def entry_to_feature(entry):
    """One CMR granule entry to one GeoJSON feature, or None without a polygon."""
    polygons = entry.get("polygons")
    if not polygons or not polygons[0]:
        return None
    numbers = [float(v) for v in polygons[0][0].split()]
    # CMR lists latitude then longitude; GeoJSON wants longitude then latitude.
    ring = [[numbers[i + 1], numbers[i]] for i in range(0, len(numbers), 2)]
    if ring[0] != ring[-1]:
        ring.append(ring[0])

    start = entry.get("time_start")
    cloud = entry.get("cloud_cover")
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {
            "id": entry.get("title"),
            "start": start,
            "end": entry.get("time_end"),
            "cloud": float(cloud) if cloud not in (None, "") else None,
            "year": int(start[:4]) if start else None,
            "browse": _link(entry, _BROWSE),
            "data": _link(entry, _DATA),
        },
    }


def fetch_page(page_num):
    """Entries from one CMR page. Retries transient failures three times."""
    url = (f"{CMR_URL}?short_name={SHORT_NAME}&bounding_box={BBOX}"
           f"&page_size={PAGE_SIZE}&page_num={page_num}")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return json.load(response)["feed"]["entry"]
        except (OSError, ValueError, KeyError) as exc:
            if attempt == 2:
                raise
            print(f"page {page_num}: {exc}; retrying", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def fetch_all(fetch_page_fn):
    """Every entry, paging until a page comes back empty."""
    entries = []
    page = 1
    while True:
        got = fetch_page_fn(page)
        if not got:
            return entries
        entries.extend(got)
        print(f"page {page}: {len(got)} granules (total {len(entries)})", flush=True)
        page += 1


def write_geojson(features, path):
    """Write a FeatureCollection via a .part file and an atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    os.replace(part, path)


def main(argv=None):
    entries = fetch_all(fetch_page)
    features = [f for f in (entry_to_feature(e) for e in entries) if f is not None]
    write_geojson(features, paths.EMIT_FOOTPRINTS)
    print(f"emit: {len(features)} footprints written to {paths.EMIT_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `run.sh`, add a case after `vendor)`'s `;;`:

```bash
  emit)
    # Fetches every EMIT L2A footprint over CONUS from NASA's CMR (no login)
    # into data/emit/footprints.geojson. Rerun to refresh. Network access.
    shift; exec python3 -m viz.fetch_emit "$@" ;;
```

and change the usage line to `usage: $0 {extract|prepare|vendor|emit|serve|test} [args]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && bash -n run.sh && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_fetch_emit -v`

Expected: PASS, 7 tests. `./run.sh test` stays green.

- [ ] **Step 5: Run the real fetch**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && ./run.sh emit`

Expected: about fifteen `page N:` lines, then `emit: ~28900 footprints written`. Verify:

```bash
PYTHONNOUSERSITE=1 python3 -c "
import json, collections
g = json.load(open('data/emit/footprints.geojson'))
print(len(g['features']), 'features')
print(sorted(collections.Counter(f['properties']['year'] for f in g['features']).items()))
p = g['features'][0]['properties']; print({k: p[k] for k in ('id','start','cloud','year')})
print('lon/lat order ok:', -130 < g['features'][0]['geometry']['coordinates'][0][0][0] < -60)"
ls -la data/emit/
```

Expected: a count near 28,900, per-year counts close to the spec's table, a plausible first property set, `lon/lat order ok: True`, and a file of a few megabytes with no `.part` beside it.

- [ ] **Step 6: Commit**

```bash
git add viz/fetch_emit.py tests/test_fetch_emit.py run.sh
git commit -m "Fetch EMIT L2A footprints over CONUS from CMR"
```

---

### Task 3: Server routes and catalog fields

**Files:**
- Modify: `viz/tileserver.py`, `run.sh` (`serve` case), `docs/superpowers/specs/2026-09-21-emit-footprints-design.md` (one paragraph)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `emit.index_for(path)`, `emit.week_sunday`, `paths.EMIT_FOOTPRINTS`
- Produces:
  - `GET /api/emit/footprints.geojson` → the file with `application/geo+json`, or 404 naming `./run.sh emit`
  - `/api/catalog` gains `emit_count: int` and `emit_fetched: str | null`
  - `/api/point` accepts optional `week`; response gains `emit: list[dict]` (newest first) and `week_sunday: str | null` (ISO date)

- [ ] **Step 1: Write the failing test**

In `tests/test_server.py`, inside `ServerTestCase.setUpClass`, extend the saved/swapped constants and write a fixture. Change the two tuples:

```python
        cls._saved = (paths.DATA, paths.CPC_DATA, paths.CDL_DATA, paths.MASK_DATA,
                      paths.CATALOG, paths.TILE_CACHE, paths.EMIT_FOOTPRINTS)
        ...
        paths.TILE_CACHE = cls.tmp / "cache" / "tiles"
        paths.EMIT_FOOTPRINTS = paths.DATA / "emit" / "footprints.geojson"
```

and in `tearDownClass` restore seven values. After the `cls.lon, cls.lat = ...` line, add a fixture footprint around the fixture centre plus one far away:

```python
        def square(lon, lat, half):
            return [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
                    [lon - half, lat + half], [lon - half, lat - half]]

        def feature(fid, ring, start, cloud):
            return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"id": fid, "start": start, "end": start, "cloud": cloud,
                                   "year": int(start[:4]), "browse": "https://b/x.png", "data": None}}

        paths.EMIT_FOOTPRINTS.parent.mkdir(parents=True)
        paths.EMIT_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": [
            feature("near-old", square(cls.lon, cls.lat, 0.5), "2023-07-01T18:00:00Z", 12.0),
            feature("near-new", square(cls.lon, cls.lat, 0.5), "2025-07-20T18:00:00Z", 40.0),
            feature("far", square(cls.lon + 20, cls.lat, 0.5), "2024-07-01T18:00:00Z", 1.0),
        ]}))
```

Add a test class:

```python
class TestEmitRoutes(ServerTestCase):
    def test_footprints_are_served_as_geojson(self):
        status, ctype, body = self.get("/api/emit/footprints.geojson")
        self.assertEqual(status, 200)
        self.assertIn("geo+json", ctype)
        self.assertEqual(len(json.loads(body)["features"]), 3)

    def test_catalog_reports_emit_count_and_fetched(self):
        catalog = json.loads(self.get("/api/catalog")[2])
        self.assertEqual(catalog["emit_count"], 3)
        self.assertRegex(catalog["emit_fetched"], r"^\d{4}-\d{2}-\d{2}T")

    def test_point_lists_covering_granules_newest_first(self):
        report = json.loads(self.get(self.point_url())[2])
        self.assertEqual([g["id"] for g in report["emit"]], ["near-new", "near-old"])
        self.assertEqual(report["emit"][0]["cloud"], 40.0)

    def test_point_far_from_footprints_has_empty_emit(self):
        url = (f"/api/point?lon={self.lon + 40:.6f}&lat={self.lat:.6f}"
               "&crop=corn&year=2024&cdl_year=2024")
        self.assertEqual(json.loads(self.get(url)[2])["emit"], [])

    def test_point_returns_week_sunday_when_week_given(self):
        report = json.loads(self.get(self.point_url() + "&week=15")[2])
        self.assertEqual(report["week_sunday"], "2024-04-14")

    def test_point_without_week_has_null_week_sunday(self):
        self.assertIsNone(json.loads(self.get(self.point_url())[2])["week_sunday"])

    def test_missing_footprints_file_gives_404_and_empty_emit(self):
        moved = paths.EMIT_FOOTPRINTS.with_name("moved.geojson")
        paths.EMIT_FOOTPRINTS.rename(moved)
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/emit/footprints.geojson")
            self.assertEqual(ctx.exception.code, 404)
            self.assertEqual(json.loads(self.get(self.point_url())[2])["emit"], [])
            self.assertEqual(json.loads(self.get("/api/catalog")[2])["emit_count"], 0)
        finally:
            moved.rename(paths.EMIT_FOOTPRINTS)
```

`point_url()` exists on `TestPointRoute`; move it up to `ServerTestCase` so both classes share it (it uses `self.lon`/`self.lat` and hardcodes `crop=corn&year=2024&cdl_year=2024`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestEmitRoutes -v`

Expected: FAIL/ERROR on every test (404 route, missing keys, `AttributeError: EMIT_FOOTPRINTS`).

- [ ] **Step 3: Write minimal implementation**

In `viz/tileserver.py`:

1. Import: change `from viz import color, naming, paths, rasters` to `from viz import color, emit, naming, paths, rasters`.

2. In `point_report`, change the signature to `def point_report(lon, lat, crop, year, cdl_year, week=None):` and, before the `return {`, add:

```python
    index = emit.index_for(paths.EMIT_FOOTPRINTS)
    granules = index.covering(lon, lat) if index else []
    sunday = emit.week_sunday(year, week).isoformat() if week else None
```

and two keys to the returned dict: `"emit": granules,` and `"week_sunday": sunday,`.

3. In the `/api/catalog` branch, after `catalog["server_token"] = SERVER_TOKEN`:

```python
                index = emit.index_for(paths.EMIT_FOOTPRINTS)
                catalog["emit_count"] = index.count if index else 0
                catalog["emit_fetched"] = index.fetched if index else None
```

4. Add a route before the `/api/point` branch:

```python
            if route == "/api/emit/footprints.geojson":
                if not paths.EMIT_FOOTPRINTS.is_file():
                    return self._fail(HTTPStatus.NOT_FOUND, "no EMIT footprints; run ./run.sh emit")
                return self._send(paths.EMIT_FOOTPRINTS.read_bytes(), CONTENT_TYPES[".geojson"])
```

5. In `_handle_point`, parse the optional week inside the existing `try` that converts numerics:

```python
            week = int(query["week"][0]) if "week" in query else None
```

and pass `week` as the last argument to `point_report`.

In `run.sh`'s `serve` case, after the vendor guard:

```bash
    if [ ! -f data/emit/footprints.geojson ]; then
      echo "note: no EMIT footprints (data/emit/footprints.geojson); the EMIT layer is off until ./run.sh emit is run" >&2
    fi
```

In the spec, replace the `viz/prepare.py` row of the table in §4 with `| \`viz/tileserver.py\` | Computes \`emit_count\` and \`emit_fetched\` from the file at request time |` and in §4.3 replace "The catalog gains `emit_count` and `emit_fetched` (an ISO timestamp, or null) so the interface can enable the control only when data exists." with "The catalog route computes `emit_count` and `emit_fetched` (the file's modification time as an ISO timestamp, or null) from the file on each request, so a fresh `./run.sh emit` is visible without rerunning `prepare`."

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server -v`

Expected: PASS. Then `./run.sh test` green. Then restart the running server and probe:

```bash
pkill -f 'python3 -m viz[.]tileserver'; sleep 1; nohup ./run.sh serve > /tmp/serve.log 2>&1 &
sleep 3
curl -s http://127.0.0.1:8000/api/catalog | python3 -c "import json,sys; c=json.load(sys.stdin); print(c['emit_count'], c['emit_fetched'])"
curl -s 'http://127.0.0.1:8000/api/point?lon=-93.62&lat=42.03&crop=corn&year=2025&cdl_year=2025&week=30' | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['week_sunday'], len(r['emit']), 'granules;', [g['start'][:10] for g in r['emit'][:3]])"
```

Expected: a count near 28,900 with a timestamp; `2025-07-27`, a non-zero granule count for Ames, dates descending.

- [ ] **Step 5: Commit**

```bash
git add viz/tileserver.py run.sh tests/test_server.py docs/superpowers/specs/2026-09-21-emit-footprints-design.md
git commit -m "Serve EMIT footprints and list covering granules in point queries"
```

---

### Task 4: Layer, controls, legend, tooltip, and popup section

**Files:**
- Modify: `viz/web/app.js`, `viz/web/index.html`, `viz/web/style.css`
- Test: `tests/test_server.py` (static assertions)

**Interfaces:**
- Consumes: `/api/emit/footprints.geojson`, catalog `emit_count`, `/api/point` `emit` and `week_sunday`
- Produces: the interface

- [ ] **Step 1: Write the failing test**

Add to `TestInterfaceAssets` in `tests/test_server.py`:

```python
    def test_emit_layer_controls_and_canvas_renderer(self):
        _, _, index = self.get("/")
        html = index.decode()
        for ident in ('id="emit"', 'id="emitCloud"', 'id="emitWindow"', 'id="emitLegend"'):
            self.assertIn(ident, html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("/api/emit/footprints.geojson", text)
        self.assertIn("L.canvas(", text)
        self.assertIn("EMIT_YEAR_COLOURS", text)
        self.assertIn("2024-04-14", text)  # the week-mapping anchor, mirrored from viz/emit.py
        self.assertIn("&week=", text)
        self.assertIn("EMIT scenes covering this point", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestInterfaceAssets -v`

Expected: FAIL on the new test.

- [ ] **Step 3: index.html**

After the `State boundaries` checkbox label, add:

```html
    <label class="inline"><input type="checkbox" id="emit"> EMIT footprints</label>
    <div class="sub" id="emitControls">
      <label>Max cloud cover <output id="emitCloudOut">30%</output>
        <input type="range" id="emitCloud" min="0" max="100" step="5" value="30">
      </label>
      <label>Window ±days <output id="emitWindowOut">7</output>
        <input type="range" id="emitWindow" min="0" max="30" step="1" value="7">
      </label>
      <div id="emitLegend"></div>
    </div>
```

- [ ] **Step 4: style.css**

Append:

```css
/* EMIT footprints */
#emitControls { display: grid; gap: 7px; padding-left: 22px; }
#emitControls.disabled { opacity: 0.5; }
#emitLegend { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 11px; color: var(--muted); }
#emitLegend span::before {
  content: ""; display: inline-block; width: 14px; height: 0; margin-right: 4px;
  border-top: 2px solid var(--swatch); vertical-align: middle;
}
#emitLegend span.hl::before { border-top-width: 3px; }
.leaflet-emit-pane { cursor: default; }
.emit-tip { font-size: 11px; }
.readout-popup .emit-list { margin: 4px 0 0; padding: 0; list-style: none; }
.readout-popup .emit-list li { display: flex; gap: 6px; align-items: baseline; padding: 1px 0; }
.readout-popup .emit-list .when { font-variant-numeric: tabular-nums; }
.readout-popup .emit-list .in { font-weight: 650; }
.readout-popup .emit-list a { color: var(--accent); }
```

- [ ] **Step 5: app.js**

1. After the `map.getPane("states").style.zIndex = 460;` line:

```javascript
  map.createPane("emit");
  map.getPane("emit").style.zIndex = 455;   // above CPC, below state lines
  var emitRenderer = L.canvas({ pane: "emit" });

  // Validated categorical palette; fixed order, never cycled within the mission.
  var EMIT_YEAR_COLOURS = { 2022: "#1c5cab", 2023: "#8b45d9", 2024: "#e0338e", 2025: "#00a3c4", 2026: "#b5651d" };
  var EMIT_HIGHLIGHT = "#111111";
  var emitLayer = null;      // L.geoJSON over the whole file
  var emitLoaded = false;
```

2. After `var LABEL_MAX_ZOOM = 9;` block, add the state fields to `state`: in the `var state = {` literal add

```javascript
    emit: false,
    emitCloud: 30,
    emitWindow: 7,
```

3. Add these functions before `function drawLegend() {`:

```javascript
  // Sunday ending ISO week N. Mirrors viz/emit.py: 2024 week 15 -> 2024-04-14.
  function weekSunday(year, week) {
    var jan4 = new Date(Date.UTC(year, 0, 4));
    var jan4Dow = jan4.getUTCDay() || 7;             // Monday=1 .. Sunday=7
    var week1Monday = new Date(jan4.getTime() - (jan4Dow - 1) * 86400000);
    return new Date(week1Monday.getTime() + ((week - 1) * 7 + 6) * 86400000);
  }

  function emitWindowBounds() {
    if (!state.emitWindow || state.week === null) { return null; }
    var centre = weekSunday(state.year, state.week).getTime();
    var span = state.emitWindow * 86400000;
    return [centre - span, centre + span];
  }

  function emitStyle(feature) {
    var p = feature.properties;
    if (p.cloud !== null && p.cloud > state.emitCloud) { return { stroke: false, fill: false }; }
    var bounds = emitWindowBounds();
    var t = Date.parse(p.start);
    var inWindow = bounds && t >= bounds[0] && t <= bounds[1];
    return {
      renderer: emitRenderer, fill: false,
      color: inWindow ? EMIT_HIGHLIGHT : (EMIT_YEAR_COLOURS[p.year] || "#666"),
      weight: inWindow ? 2.5 : 1,
      opacity: inWindow ? 0.95 : (bounds ? 0.35 : 0.8)
    };
  }

  function loadEmit() {
    if (emitLoaded || !(state.catalog.emit_count > 0)) { return; }
    emitLoaded = true;
    fetch("/api/emit/footprints.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      emitLayer = L.geoJSON(geo, {
        pane: "emit", renderer: emitRenderer, style: emitStyle,
        onEachFeature: function (f, layer) {
          var p = f.properties;
          layer.bindTooltip(p.start.slice(0, 10) + " · " + (p.cloud === null ? "?" : p.cloud + "%") + " cloud",
                            { sticky: true, className: "emit-tip" });
        }
      });
      syncEmit();
    }).catch(function () { emitLoaded = false; });
  }

  function drawEmitLegend() {
    var years = Object.keys(EMIT_YEAR_COLOURS).sort();
    el("emitLegend").innerHTML =
      years.map(function (y) { return '<span style="--swatch:' + EMIT_YEAR_COLOURS[y] + '">' + y + "</span>"; }).join("") +
      '<span class="hl" style="--swatch:' + EMIT_HIGHLIGHT + '">within window</span>';
  }

  function syncEmit() {
    var available = state.catalog.emit_count > 0;
    var box = el("emit");
    box.disabled = !available;
    box.parentNode.title = available ? "" : "No EMIT footprints; run ./run.sh emit";
    el("emitControls").classList.toggle("disabled", !(available && state.emit));
    el("emitCloud").disabled = el("emitWindow").disabled = !(available && state.emit);
    if (!state.emit && emitLayer && map.hasLayer(emitLayer)) { map.removeLayer(emitLayer); }
    if (state.emit) {
      if (!emitLayer) { loadEmit(); return; }
      if (!map.hasLayer(emitLayer)) { emitLayer.addTo(map); }
      emitLayer.setStyle(emitStyle);
    }
  }
```

4. In `refresh()`, after `drawPairing();` add `syncEmit();` (the window follows the week).

5. In `wire()`, after `map.on("zoomend", syncStates);`:

```javascript
    el("emit").addEventListener("change", function (e) { state.emit = e.target.checked; syncEmit(); });
    el("emitCloud").addEventListener("input", function (e) {
      state.emitCloud = Number(e.target.value); el("emitCloudOut").textContent = e.target.value + "%"; syncEmit();
    });
    el("emitWindow").addEventListener("input", function (e) {
      state.emitWindow = Number(e.target.value);
      el("emitWindowOut").textContent = e.target.value === "0" ? "off" : e.target.value;
      syncEmit();
    });
```

6. In the click handler, append `+ "&week=" + state.week` to the `url` (after `cdl_year=`), guarded: use `(state.week !== null ? "&week=" + state.week : "")`.

7. In `showReadout`, before `return html;`:

```javascript
    var granules = report.emit || [];
    html += '<p class="section">EMIT scenes covering this point: ' + granules.length + "</p>";
    if (granules.length) {
      var centre = report.week_sunday ? Date.parse(report.week_sunday + "T00:00:00Z") : null;
      var bounds = emitWindowBounds();
      var sorted = granules.slice().sort(function (a, b) {
        if (centre === null) { return Date.parse(b.start) - Date.parse(a.start); }
        return Math.abs(Date.parse(a.start) - centre) - Math.abs(Date.parse(b.start) - centre);
      });
      var shown = sorted.slice(0, 15);
      html += '<ul class="emit-list">' + shown.map(function (g) {
        var t = Date.parse(g.start);
        var inWin = bounds && t >= bounds[0] && t <= bounds[1];
        return "<li" + (inWin ? ' class="in"' : "") + '><span class="when">' + g.start.slice(0, 10) + "</span>" +
               "<span>" + (g.cloud === null ? "?" : g.cloud.toFixed(0) + "%") + " cloud</span>" +
               (g.browse ? ' <a href="' + g.browse + '" target="_blank" rel="noopener">browse</a>' : "") +
               (g.data ? ' <a href="' + g.data + '" target="_blank" rel="noopener">data</a>' : "") +
               (inWin ? " <span>★</span>" : "") + "</li>";
      }).join("") + "</ul>";
      if (granules.length > shown.length) {
        html += '<p class="note">and ' + (granules.length - shown.length) + " more</p>";
      }
    }
```

8. In the catalog `fetch(...).then(function (catalog) {` block, after `loadStates();` add `drawEmitLegend(); syncEmit();`.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && node --check viz/web/app.js && PYTHONNOUSERSITE=1 python3 -m unittest discover -s tests -t . 2>&1 | tail -1`

Expected: `OK`. No server restart is needed for these three files.

- [ ] **Step 7: Browser check (for the user)**

1. A new "EMIT footprints" checkbox, unticked, with dimmed cloud and window sliders and a year legend beneath.
2. Tick it: thousands of thin outlines appear across CONUS in five year colours; panning and zooming stay smooth.
3. Lower "Max cloud cover" to 10%: most outlines vanish; raise to 100%: all return.
4. With corn / 2025 / week 30 selected and the window at 7: scenes from 20–27 July to 3 August 2025 draw in bold black; the rest fade. Step the week: the bold set moves. Set the window to 0: no highlight, all outlines at full strength.
5. Hover an outline: a tooltip with date and cloud %.
6. Click in Iowa: the popup ends with "EMIT scenes covering this point: N", up to 15 rows sorted by closeness to the week, starred inside the window, with browse and data links that open in a new tab.
7. Untick the checkbox: outlines vanish, sliders dim.

- [ ] **Step 8: Commit**

```bash
git add viz/web/app.js viz/web/index.html viz/web/style.css tests/test_server.py
git commit -m "Draw EMIT footprints with cloud and CPC-week filters and list them in the readout"
```

---

## Self-Review

**Spec coverage.** §2 (source) informs Task 2's constants. §3 (week mapping) is `emit.week_sunday` (Task 1), mirrored in JS (Task 4), both tested against the anchor. §4.1 (fetch) is Task 2. §4.2 (point lookup) is Task 1's index and Task 3's wiring. §4.3 (server) is Task 3, with the catalog fields computed at request time and the spec amended to say so. §4.4 (layer and controls) and §4.5 (popup) are Task 4. §5 (testing) is distributed. §6 (out of scope) is honoured.

**Placeholder scan.** Every step carries runnable content; no "TBD", no "similar to Task N".

**Type consistency.** `emit.index_for(path)` returns `FootprintIndex | None` and Task 3 guards `if index`. `covering()` returns property dicts with `id`, `start`, `end`, `cloud`, `year`, `browse`, `data`, which is exactly what `entry_to_feature` writes and what `showReadout` reads. `week_sunday` takes `(year, week)` in both languages. The catalog keys `emit_count` and `emit_fetched` are written in Task 3 and read in Task 4's `syncEmit`. The `/api/point` `week` parameter is parsed in Task 3 and sent in Task 4.
