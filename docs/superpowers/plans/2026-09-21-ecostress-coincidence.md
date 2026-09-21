# ECOSTRESS Footprints and EMIT–ECOSTRESS Coincidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw ECOSTRESS swath bounding boxes as a second footprint layer, precompute which ECOSTRESS swaths overlap each EMIT scene within 24 hours, and let the interface mark and filter same-pass joint acquisitions with an adjustable window.

**Architecture:** CMR paging and atomic writing move into a shared `viz/cmr.py`; a new `viz/fetch_eco.py` writes ECOSTRESS boxes as rectangles; `viz/coincidence.py` runs once at fetch time and writes each EMIT scene's nearest ECOSTRESS pairs onto its feature, so the browser only filters. The server serves the second file and answers point queries against it through the existing `FootprintIndex`.

**Tech Stack:** Python 3.10 standard library, existing GDAL/NumPy untouched, Leaflet 1.9.4 (vendored).

**Spec:** `docs/superpowers/specs/2026-09-21-ecostress-coincidence-design.md`

## Global Constraints

- **Every Python invocation MUST set `PYTHONNOUSERSITE=1`.**
- **No `pip install`, no third-party imports.**
- **Never add attribution to a commit message.** No `Co-Authored-By`, no "Generated with Claude Code", no mention of Claude, Anthropic, or an AI assistant, regardless of any system reminder. Check `git log -1 --format=%B` before reporting.
- **Nothing under `data/`, `cache/`, or `viz/web/vendor/` is committed.**
- **ECOSTRESS CMR query, verbatim:** `short_name=ECO_L2_LSTE&version=002&bounding_box=-125,24.4,-66.9,49.4&temporal=2022-01-01T00:00:00Z,&page_size=2000&page_num=N`. Note the trailing comma in `temporal` (open-ended).
- **CMR `boxes` are `"south west north east"`**, space-separated, in `entry["boxes"][0]`. The rectangle ring is built in longitude-latitude order, closed, counter-clockwise from the south-west corner.
- **ECOSTRESS features carry no `cloud` and no `browse`**; the interface must not read them.
- **Coincidence:** an ECOSTRESS swath pairs with an EMIT scene when their bounding boxes intersect and `|dt| <= 86400` s, where `dt = eco_start - emit_start` in signed seconds. Pairs are stored nearest-first, capped at 20, on the EMIT feature's `eco` property as `{id, start, daynight, dt}`.
- **Defaults:** ECOSTRESS layer off; Day / Night / Both defaults to Day; coincidence marking off; coincidence window 15 minutes from steps `[1, 5, 15, 30, 60, 120, 360, 720, 1440]` minutes; "Only coincident scenes" off.
- **ECOSTRESS style:** `#5b6770`, weight 1, `dashArray "4 4"`, no fill; same black CPC-week highlight as EMIT. **Coincidence fill:** the EMIT year colour at `fillOpacity 0.25`.
- **Pane order:** eco 452, emit 455, states 460.
- Server restart after any Python change. A server runs on port 8000; probe on 8765 and stop it.

## File Structure

| Path | Responsibility |
| --- | --- |
| `viz/cmr.py` | Paged CMR granule search, link helper, atomic GeoJSON write |
| `viz/fetch_emit.py` | EMIT fetch, now importing from `cmr.py`; unchanged interface |
| `viz/fetch_eco.py` | ECOSTRESS swath fetch into `data/eco/footprints.geojson` |
| `viz/coincidence.py` | Sweep pairing EMIT scenes with overlapping ECOSTRESS swaths; rewrites the EMIT file |
| `viz/paths.py` | `ECO_DATA`, `ECO_FOOTPRINTS` |
| `viz/emit.py` | `FootprintIndex.count_where(predicate)` |
| `viz/tileserver.py` | ECOSTRESS route, `eco` on `/api/point`, three catalog fields |
| `run.sh` | `footprints` subcommand (EMIT, ECOSTRESS, coincidence); `emit` aliases it |
| `viz/web/*` | ECOSTRESS layer and controls, coincidence controls and styling, popup additions |
| `tests/test_cmr.py`, `tests/test_fetch_eco.py`, `tests/test_coincidence.py` | Tasks 1–3 |
| `tests/test_server.py`, `tests/test_emit.py` | Task 4 additions |

---

### Task 1: Shared CMR module

**Files:**
- Create: `viz/cmr.py`
- Modify: `viz/fetch_emit.py` (import from `cmr`, drop the duplicated functions)
- Test: `tests/test_cmr.py`

**Interfaces:**
- Produces:
  - `cmr.CMR_URL: str`, `cmr.BBOX: str` = `"-125,24.4,-66.9,49.4"`, `cmr.PAGE_SIZE: int` = 2000
  - `cmr.page_url(short_name, page_num, version=None, temporal=None) -> str`
  - `cmr.fetch_page(url) -> list[dict]` with three retries
  - `cmr.fetch_all(fetch_page_fn) -> list[dict]` — stops on the first empty page
  - `cmr.link(entry, suffix) -> str | None`
  - `cmr.write_geojson(features, path) -> None` — `.part` then `os.replace`
  - `fetch_emit.fetch_all`, `fetch_emit.write_geojson`, `fetch_emit.fetch_page` remain importable (re-exported) so `tests/test_fetch_emit.py` passes unchanged

- [ ] **Step 1: Write the failing test**

```python
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import cmr


class TestPageUrl(unittest.TestCase):
    def test_emit_style_url(self):
        url = cmr.page_url("EMITL2ARFL", 3)
        self.assertTrue(url.startswith(cmr.CMR_URL + "?"))
        self.assertIn("short_name=EMITL2ARFL", url)
        self.assertIn("bounding_box=-125,24.4,-66.9,49.4", url)
        self.assertIn("page_size=2000", url)
        self.assertIn("page_num=3", url)
        self.assertNotIn("version=", url)
        self.assertNotIn("temporal=", url)

    def test_ecostress_style_url_with_version_and_open_temporal(self):
        url = cmr.page_url("ECO_L2_LSTE", 1, version="002", temporal="2022-01-01T00:00:00Z,")
        self.assertIn("version=002", url)
        self.assertIn("temporal=2022-01-01T00:00:00Z,", url)


class TestFetchAll(unittest.TestCase):
    def test_pages_until_an_empty_page(self):
        pages = {1: [{"a": 1}, {"a": 2}], 2: [{"a": 3}], 3: []}
        asked = []

        def fake(page_num):
            asked.append(page_num)
            return pages.get(page_num, [])

        self.assertEqual(len(cmr.fetch_all(fake)), 3)
        self.assertEqual(asked, [1, 2, 3])


class TestLink(unittest.TestCase):
    ENTRY = {"links": [
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/s3#", "href": "s3://bucket/x.nc"},
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://d/x.nc"},
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/browse#", "href": "https://d/x.png"},
    ]}

    def test_picks_first_http_href_for_suffix(self):
        self.assertEqual(cmr.link(self.ENTRY, "/data#"), "https://d/x.nc")
        self.assertEqual(cmr.link(self.ENTRY, "/browse#"), "https://d/x.png")

    def test_missing_suffix_is_none(self):
        self.assertIsNone(cmr.link(self.ENTRY, "/metadata#"))

    def test_s3_only_is_none(self):
        entry = {"links": [{"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "s3://b/x"}]}
        self.assertIsNone(cmr.link(entry, "/data#"))


class TestWriteGeojson(unittest.TestCase):
    def test_atomic_feature_collection(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        out = tmp / "sub" / "f.geojson"
        cmr.write_geojson([{"type": "Feature", "geometry": None, "properties": {}}], out)
        self.assertEqual(json.loads(out.read_text())["type"], "FeatureCollection")
        self.assertFalse(list(tmp.glob("**/*.part")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_cmr -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'viz.cmr'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/cmr.py`:

```python
"""Shared access to NASA's Common Metadata Repository granule search.

Used by the EMIT and ECOSTRESS fetch scripts. These are the project's only
network steps; the interface never touches the network.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
BBOX = "-125,24.4,-66.9,49.4"
PAGE_SIZE = 2000


def page_url(short_name, page_num, version=None, temporal=None):
    """One page of a granule search over the CONUS box."""
    parts = [f"short_name={short_name}"]
    if version:
        parts.append(f"version={version}")
    parts.append(f"bounding_box={BBOX}")
    if temporal:
        parts.append(f"temporal={temporal}")
    parts.append(f"page_size={PAGE_SIZE}")
    parts.append(f"page_num={page_num}")
    return CMR_URL + "?" + "&".join(parts)


def fetch_page(url):
    """Entries from one CMR page. Retries transient failures three times."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return json.load(response)["feed"]["entry"]
        except (OSError, ValueError, KeyError) as exc:
            if attempt == 2:
                raise
            print(f"{url[-40:]}: {exc}; retrying", file=sys.stderr)
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


def link(entry, suffix):
    """First http(s) href whose rel ends with suffix, or None."""
    for item in entry.get("links", []):
        href = item.get("href", "")
        if item.get("rel", "").endswith(suffix) and href.startswith("http"):
            return href
    return None


def write_geojson(features, path):
    """Write a FeatureCollection via a .part file and an atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    os.replace(part, path)
```

Rewrite `viz/fetch_emit.py` to use it, keeping the same public names:

```python
"""Fetch every EMIT L2A reflectance footprint over CONUS from NASA's CMR.

Run by ./run.sh footprints (and its alias ./run.sh emit). Each run refetches
in full, about fifteen requests of 2,000 granules, and rewrites the file
atomically. Coincidence with ECOSTRESS is computed afterwards by
viz/coincidence.py.
"""

import sys

from viz import cmr, paths
from viz.cmr import fetch_all, write_geojson  # re-exported for tests and callers

SHORT_NAME = "EMITL2ARFL"

_BROWSE = "/browse#"
_DATA = "/data#"


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
            "browse": cmr.link(entry, _BROWSE),
            "data": cmr.link(entry, _DATA),
        },
    }


def fetch_page(page_num):
    return cmr.fetch_page(cmr.page_url(SHORT_NAME, page_num))


def main(argv=None):
    entries = fetch_all(fetch_page)
    features = [f for f in (entry_to_feature(e) for e in entries) if f is not None]
    write_geojson(features, paths.EMIT_FOOTPRINTS)
    print(f"emit: {len(features)} footprints written to {paths.EMIT_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_cmr tests.test_fetch_emit -v`
Expected: PASS, both modules; the EMIT tests pass unchanged because the names are re-exported. Then `./run.sh test` stays green.

- [ ] **Step 5: Commit**

```bash
git add viz/cmr.py viz/fetch_emit.py tests/test_cmr.py
git commit -m "Move CMR paging and GeoJSON writing into a shared module"
```

---

### Task 2: ECOSTRESS fetch

**Files:**
- Create: `viz/fetch_eco.py`
- Modify: `viz/paths.py` (two lines), `run.sh` (`footprints` case; `emit` becomes an alias; usage line; serve note)
- Test: `tests/test_fetch_eco.py`

**Interfaces:**
- Consumes: `cmr.page_url`, `cmr.fetch_page`, `cmr.fetch_all`, `cmr.write_geojson`
- Produces:
  - `paths.ECO_DATA: Path` = `DATA / "eco"`, `paths.ECO_FOOTPRINTS: Path` = `ECO_DATA / "footprints.geojson"`
  - `fetch_eco.SHORT_NAME` = `"ECO_L2_LSTE"`, `fetch_eco.VERSION` = `"002"`, `fetch_eco.TEMPORAL` = `"2022-01-01T00:00:00Z,"`
  - `fetch_eco.box_to_ring(box: str) -> list[list[float]]` — closed rectangle, `[lon, lat]`, from `"south west north east"`
  - `fetch_eco.entry_to_feature(entry) -> dict | None` with properties `id`, `start`, `end`, `daynight`, `year`, `orbit`
  - `fetch_eco.main(argv=None) -> int`
  - `./run.sh footprints` (EMIT fetch, ECOSTRESS fetch, then coincidence once Task 3 lands); `./run.sh emit` aliases it

- [ ] **Step 1: Write the failing test**

```python
import json
import unittest

from viz import fetch_eco

ENTRY = {
    "title": "ECOv002_L2_LSTE_39898_003_20250720T002641_0713_01",
    "time_start": "2025-07-20T00:26:41.997Z",
    "time_end": "2025-07-20T00:27:33.966Z",
    "day_night_flag": "DAY",
    "boxes": ["25.5522193 -89.8633255 30.4788793 -84.1269103"],
    "orbit_calculated_spatial_domains": [{"start_orbit_number": "39898", "stop_orbit_number": "39898"}],
    "links": [],
}


class TestBoxToRing(unittest.TestCase):
    def test_rectangle_in_lon_lat_order_closed(self):
        ring = fetch_eco.box_to_ring("25.5 -89.9 30.5 -84.1")
        self.assertEqual(ring[0], [-89.9, 25.5])   # south-west
        self.assertEqual(ring[1], [-84.1, 25.5])   # south-east
        self.assertEqual(ring[2], [-84.1, 30.5])   # north-east
        self.assertEqual(ring[3], [-89.9, 30.5])   # north-west
        self.assertEqual(ring[4], ring[0])
        self.assertEqual(len(ring), 5)


class TestEntryToFeature(unittest.TestCase):
    def test_properties(self):
        props = fetch_eco.entry_to_feature(ENTRY)["properties"]
        self.assertEqual(props["id"], ENTRY["title"])
        self.assertEqual(props["start"], "2025-07-20T00:26:41.997Z")
        self.assertEqual(props["end"], "2025-07-20T00:27:33.966Z")
        self.assertEqual(props["daynight"], "DAY")
        self.assertEqual(props["year"], 2025)
        self.assertEqual(props["orbit"], 39898)
        self.assertNotIn("cloud", props)
        self.assertNotIn("browse", props)

    def test_geometry_is_the_box_rectangle(self):
        ring = fetch_eco.entry_to_feature(ENTRY)["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], [-89.8633255, 25.5522193])

    def test_entry_without_box_is_skipped(self):
        entry = dict(ENTRY); del entry["boxes"]
        self.assertIsNone(fetch_eco.entry_to_feature(entry))

    def test_missing_orbit_is_none(self):
        entry = dict(ENTRY); del entry["orbit_calculated_spatial_domains"]
        self.assertIsNone(fetch_eco.entry_to_feature(entry)["properties"]["orbit"])

    def test_unknown_daynight_is_kept_verbatim(self):
        entry = dict(ENTRY, day_night_flag="BOTH")
        self.assertEqual(fetch_eco.entry_to_feature(entry)["properties"]["daynight"], "BOTH")


class TestQuery(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(fetch_eco.SHORT_NAME, "ECO_L2_LSTE")
        self.assertEqual(fetch_eco.VERSION, "002")
        self.assertEqual(fetch_eco.TEMPORAL, "2022-01-01T00:00:00Z,")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_fetch_eco -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'viz.fetch_eco'`

- [ ] **Step 3: Write minimal implementation**

Append to `viz/paths.py` after the `EMIT_FOOTPRINTS` line:

```python
ECO_DATA = DATA / "eco"
ECO_FOOTPRINTS = ECO_DATA / "footprints.geojson"
```

Create `viz/fetch_eco.py`:

```python
"""Fetch ECOSTRESS swath scene bounding boxes over CONUS from NASA's CMR.

Run by ./run.sh footprints. The swath product (ECO_L2_LSTE v002) has one
granule per acquisition but carries only a bounding box in CMR, about 6 x 5
degrees for a 52-second scene against a real swath of roughly 400 km; no
polygon and no cloud cover. Boxes are written as rectangle polygons and are
labelled as approximate in the interface. Fetched from 2022 onward to match
EMIT's span.
"""

import sys

from viz import cmr, paths

SHORT_NAME = "ECO_L2_LSTE"
VERSION = "002"
TEMPORAL = "2022-01-01T00:00:00Z,"   # open-ended: 2022 to now


def box_to_ring(box):
    """CMR 'south west north east' to a closed [lon, lat] rectangle ring."""
    south, west, north, east = (float(v) for v in box.split())
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def entry_to_feature(entry):
    """One CMR swath entry to one GeoJSON feature, or None without a box."""
    boxes = entry.get("boxes")
    if not boxes:
        return None
    start = entry.get("time_start")
    domains = entry.get("orbit_calculated_spatial_domains") or []
    orbit = domains[0].get("start_orbit_number") if domains else None
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [box_to_ring(boxes[0])]},
        "properties": {
            "id": entry.get("title"),
            "start": start,
            "end": entry.get("time_end"),
            "daynight": entry.get("day_night_flag"),
            "year": int(start[:4]) if start else None,
            "orbit": int(orbit) if orbit not in (None, "") else None,
        },
    }


def fetch_page(page_num):
    return cmr.fetch_page(cmr.page_url(SHORT_NAME, page_num, version=VERSION, temporal=TEMPORAL))


def main(argv=None):
    entries = cmr.fetch_all(fetch_page)
    features = [f for f in (entry_to_feature(e) for e in entries) if f is not None]
    cmr.write_geojson(features, paths.ECO_FOOTPRINTS)
    print(f"eco: {len(features)} swath boxes written to {paths.ECO_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `run.sh`, replace the `emit)` case with:

```bash
  footprints|emit)
    # Fetches EMIT L2A footprints and ECOSTRESS swath boxes over CONUS from
    # NASA's CMR (no login), then pairs them. Rerun to refresh. Network access.
    shift
    python3 -m viz.fetch_emit "$@" && python3 -m viz.fetch_eco "$@" && \
      { [ -f viz/coincidence.py ] && python3 -m viz.coincidence "$@" || true; }
    exit $? ;;
```

(The coincidence step is guarded until Task 3 creates the module.) Update the usage line to `{extract|prepare|vendor|footprints|serve|test}`, and in the `serve)` case add after the EMIT note:

```bash
    if [ ! -f data/eco/footprints.geojson ]; then
      echo "note: no ECOSTRESS footprints (data/eco/footprints.geojson); the ECOSTRESS layer is off until ./run.sh footprints is run" >&2
    fi
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && bash -n run.sh && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_fetch_eco -v`
Expected: PASS, 8 tests. `./run.sh test` green.

- [ ] **Step 5: Run the real ECOSTRESS fetch only**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m viz.fetch_eco`
Expected: about 38 `page N:` lines, then `eco: ~75000 swath boxes written`. Verify:

```bash
PYTHONNOUSERSITE=1 python3 -c "
import json, collections
g = json.load(open('data/eco/footprints.geojson'))
f = g['features']; print(len(f), 'features')
print(sorted(collections.Counter(x['properties']['year'] for x in f).items()))
print(collections.Counter(x['properties']['daynight'] for x in f))
r = f[0]['geometry']['coordinates'][0]; print('ring', r, 'lon/lat ok:', -130 < r[0][0] < -60)
print({k: f[0]['properties'][k] for k in ('id','start','orbit')})"
ls -la data/eco/
```

Expected: a count near 75,000; years 2022–2026 close to the spec's table; DAY and NIGHT both present; longitude first in the ring; no `.part` file.

- [ ] **Step 6: Commit**

```bash
git add viz/fetch_eco.py viz/paths.py run.sh tests/test_fetch_eco.py
git commit -m "Fetch ECOSTRESS swath bounding boxes over CONUS from CMR"
```

---

### Task 3: Coincidence

**Files:**
- Create: `viz/coincidence.py`
- Modify: `viz/emit.py` (one method)
- Test: `tests/test_coincidence.py`, `tests/test_emit.py` (one test)

**Interfaces:**
- Consumes: `paths.EMIT_FOOTPRINTS`, `paths.ECO_FOOTPRINTS`, `cmr.write_geojson`
- Produces:
  - `coincidence.MAX_DT` = 86400, `coincidence.CAP` = 20
  - `coincidence.ring_bbox(ring) -> (minx, miny, maxx, maxy)`
  - `coincidence.intersects(a, b) -> bool` on two bboxes
  - `coincidence.parse_time(iso: str) -> float` seconds since epoch (UTC)
  - `coincidence.pair(emit_features, eco_features, max_dt=MAX_DT, cap=CAP) -> int` — sets each EMIT feature's `properties["eco"]` in place; returns the count of EMIT scenes with a pair within 900 s
  - `coincidence.main(argv=None) -> int` — reads both files, pairs, rewrites the EMIT file, prints the summary
  - `emit.FootprintIndex.count_where(predicate) -> int` — number of features whose properties satisfy the predicate

- [ ] **Step 1: Write the failing test**

`tests/test_coincidence.py`:

```python
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import coincidence


def emit_feature(fid, lon, lat, start):
    ring = [[lon - 0.4, lat - 0.4], [lon + 0.4, lat - 0.4], [lon + 0.4, lat + 0.4],
            [lon - 0.4, lat + 0.4], [lon - 0.4, lat - 0.4]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": fid, "start": start, "end": start, "cloud": 5.0,
                           "year": int(start[:4]), "browse": None, "data": None}}


def eco_feature(fid, lon, lat, start, daynight="DAY", half=3.0):
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
            [lon - half, lat + half], [lon - half, lat - half]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": fid, "start": start, "end": start, "daynight": daynight,
                           "year": int(start[:4]), "orbit": 1}}


class TestGeometryHelpers(unittest.TestCase):
    def test_ring_bbox(self):
        ring = [[-95, 40], [-94, 40], [-94, 41], [-95, 41], [-95, 40]]
        self.assertEqual(coincidence.ring_bbox(ring), (-95, 40, -94, 41))

    def test_intersects(self):
        a = (-95, 40, -94, 41)
        self.assertTrue(coincidence.intersects(a, (-94.5, 40.5, -93, 42)))
        self.assertTrue(coincidence.intersects(a, (-94, 41, -93, 42)))     # touching corner counts
        self.assertFalse(coincidence.intersects(a, (-93.9, 40, -93, 41)))

    def test_parse_time_handles_fractional_seconds_and_z(self):
        t1 = coincidence.parse_time("2025-04-22T16:47:31.716Z")
        t0 = coincidence.parse_time("2025-04-22T16:47:42.000Z")
        self.assertAlmostEqual(t1 - t0, -10.284, places=3)


class TestPair(unittest.TestCase):
    def setUp(self):
        self.emit = [emit_feature("E1", -93.6, 42.0, "2025-04-22T16:47:42Z")]
        self.eco = [
            eco_feature("same-pass", -93.0, 42.0, "2025-04-22T16:47:31Z"),          # dt -11 s, overlaps
            eco_feature("three-hours", -93.0, 42.0, "2025-04-22T19:47:42Z"),        # dt +3 h, overlaps
            eco_feature("thirty-hours", -93.0, 42.0, "2025-04-23T22:47:42Z"),       # dt +30 h, excluded
            eco_feature("elsewhere", -80.0, 30.0, "2025-04-22T16:47:50Z"),         # near in time, no overlap
            eco_feature("night", -93.0, 42.0, "2025-04-22T04:00:00Z", "NIGHT"),    # dt -12.8 h, overlaps
        ]

    def test_pairs_are_nearest_first_with_signed_dt_and_exclusions(self):
        coincidence.pair(self.emit, self.eco)
        pairs = self.emit[0]["properties"]["eco"]
        self.assertEqual([p["id"] for p in pairs], ["same-pass", "three-hours", "night"])
        self.assertEqual(pairs[0]["dt"], -11)
        self.assertEqual(pairs[1]["dt"], 3 * 3600)
        self.assertEqual(pairs[2]["daynight"], "NIGHT")
        self.assertEqual(set(pairs[0]), {"id", "start", "daynight", "dt"})

    def test_returns_count_within_fifteen_minutes(self):
        self.assertEqual(coincidence.pair(self.emit, self.eco), 1)
        far = [emit_feature("E2", -100.0, 35.0, "2025-04-22T16:47:42Z")]
        self.assertEqual(coincidence.pair(far, self.eco), 0)
        self.assertEqual(far[0]["properties"]["eco"], [])

    def test_cap(self):
        many = [eco_feature(f"e{i}", -93.0, 42.0, f"2025-04-22T16:{i:02d}:00Z") for i in range(30)]
        coincidence.pair(self.emit, many, cap=20)
        self.assertEqual(len(self.emit[0]["properties"]["eco"]), 20)

    def test_unsorted_input_is_handled(self):
        coincidence.pair(self.emit, list(reversed(self.eco)))
        self.assertEqual(self.emit[0]["properties"]["eco"][0]["id"], "same-pass")


class TestMain(unittest.TestCase):
    def test_rewrites_emit_file_with_pairs(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        from viz import paths
        saved = (paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS)
        paths.EMIT_FOOTPRINTS = tmp / "emit.geojson"
        paths.ECO_FOOTPRINTS = tmp / "eco.geojson"
        try:
            paths.EMIT_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": [
                emit_feature("E1", -93.6, 42.0, "2025-04-22T16:47:42Z")]}))
            paths.ECO_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": [
                eco_feature("same-pass", -93.0, 42.0, "2025-04-22T16:47:31Z")]}))
            self.assertEqual(coincidence.main([]), 0)
            out = json.loads(paths.EMIT_FOOTPRINTS.read_text())
            self.assertEqual(out["features"][0]["properties"]["eco"][0]["id"], "same-pass")
            self.assertFalse(list(tmp.glob("*.part")))
        finally:
            paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS = saved

    def test_main_without_eco_file_returns_1(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        from viz import paths
        saved = (paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS)
        paths.EMIT_FOOTPRINTS = tmp / "emit.geojson"
        paths.ECO_FOOTPRINTS = tmp / "absent.geojson"
        try:
            paths.EMIT_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
            self.assertEqual(coincidence.main([]), 1)
        finally:
            paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS = saved


if __name__ == "__main__":
    unittest.main()
```

Add to `tests/test_emit.py`, inside `TestFootprintIndex`:

```python
    def test_count_where(self):
        index = emit.FootprintIndex(self.path)
        self.assertEqual(index.count_where(lambda p: p["id"].startswith("n")), 1)
        self.assertEqual(index.count_where(lambda p: True), 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_coincidence tests.test_emit -v`
Expected: `test_coincidence` errors with `ModuleNotFoundError`; `test_count_where` fails with `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

Add to `viz/emit.py`, inside `FootprintIndex` after `covering`:

```python
    def count_where(self, predicate):
        """Number of footprints whose properties satisfy the predicate."""
        return sum(1 for _, _, props in self._items if predicate(props))
```

Create `viz/coincidence.py`:

```python
"""Pair each EMIT scene with the ECOSTRESS swaths that overlap it in time and space.

Both instruments ride the ISS, so a same-pass pair is the common case: EMIT's
75 km swath lies inside ECOSTRESS's 400 km swath and the two starts are seconds
apart. A pair is recorded when the ECOSTRESS box intersects the EMIT scene's
bounding box within 24 hours either way. Pairs are written onto the EMIT
feature as its "eco" property, nearest in time first, so the interface can
filter by any smaller window without recomputing.
"""

import bisect
import datetime
import json
import sys

from viz import cmr, paths

MAX_DT = 86400      # seconds either side
CAP = 20            # pairs kept per EMIT scene
SAME_PASS = 900     # seconds; reported in the summary and the catalog


def ring_bbox(ring):
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lons), min(lats), max(lons), max(lats))


def intersects(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def parse_time(iso):
    """ISO-8601 with optional fractional seconds and a Z suffix to epoch seconds."""
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def pair(emit_features, eco_features, max_dt=MAX_DT, cap=CAP):
    """Attach eco pairs to every EMIT feature. Returns EMIT scenes paired within SAME_PASS."""
    eco = sorted(
        ((parse_time(f["properties"]["start"]), ring_bbox(f["geometry"]["coordinates"][0]), f["properties"])
         for f in eco_features),
        key=lambda item: item[0],
    )
    starts = [item[0] for item in eco]

    same_pass = 0
    for feature in emit_features:
        t0 = parse_time(feature["properties"]["start"])
        box = ring_bbox(feature["geometry"]["coordinates"][0])
        lo = bisect.bisect_left(starts, t0 - max_dt)
        hi = bisect.bisect_right(starts, t0 + max_dt)
        hits = []
        for t, eco_box, props in eco[lo:hi]:
            if intersects(box, eco_box):
                hits.append({"id": props["id"], "start": props["start"],
                             "daynight": props.get("daynight"), "dt": int(round(t - t0))})
        hits.sort(key=lambda h: abs(h["dt"]))
        feature["properties"]["eco"] = hits[:cap]
        if hits and abs(hits[0]["dt"]) <= SAME_PASS:
            same_pass += 1
    return same_pass


def main(argv=None):
    if not paths.ECO_FOOTPRINTS.is_file():
        print(f"coincidence: no ECOSTRESS file at {paths.ECO_FOOTPRINTS}", file=sys.stderr)
        return 1
    emit_data = json.loads(paths.EMIT_FOOTPRINTS.read_text())
    eco_data = json.loads(paths.ECO_FOOTPRINTS.read_text())
    same_pass = pair(emit_data["features"], eco_data["features"])
    cmr.write_geojson(emit_data["features"], paths.EMIT_FOOTPRINTS)
    total = len(emit_data["features"])
    print(f"coincidence: {same_pass} of {total} EMIT scenes have an ECOSTRESS swath within "
          f"{SAME_PASS // 60} min; pairs written to {paths.EMIT_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_coincidence tests.test_emit -v`
Expected: PASS. `./run.sh test` green.

- [ ] **Step 5: Run the real pairing**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && time PYTHONNOUSERSITE=1 python3 -m viz.coincidence`
Expected: a summary such as `coincidence: N of 28882 EMIT scenes have an ECOSTRESS swath within 15 min`, with N in the tens of thousands given the 83% sample rate over cropland (it will be lower over the full CONUS set, where ECOSTRESS acquisitions are patchier), in well under a minute. Verify:

```bash
PYTHONNOUSERSITE=1 python3 -c "
import json; g = json.load(open('data/emit/footprints.geojson'))
f = g['features']; paired = [x for x in f if x['properties'].get('eco')]
print(len(f), 'EMIT;', len(paired), 'with any pair within 24 h')
p = paired[0]['properties']['eco'][0]; print('example pair:', p)
print('same-pass (<=15 min):', sum(1 for x in paired if abs(x['properties']['eco'][0]['dt']) <= 900))"
```

- [ ] **Step 6: Commit**

```bash
git add viz/coincidence.py viz/emit.py tests/test_coincidence.py tests/test_emit.py
git commit -m "Pair each EMIT scene with overlapping ECOSTRESS swaths within 24 hours"
```

---

### Task 4: Server routes and catalog fields

**Files:**
- Modify: `viz/tileserver.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `paths.ECO_FOOTPRINTS`, `emit.index_for`, `FootprintIndex.count_where`, `coincidence.SAME_PASS`
- Produces:
  - `GET /api/eco/footprints.geojson` → file or 404 naming `./run.sh footprints`
  - `/api/catalog` gains `eco_count: int`, `eco_fetched: str | null`, `coincident_15min: int`
  - `/api/point` gains `eco: list[dict]` newest first (ECOSTRESS swaths whose box contains the point); EMIT entries carry `eco` pairs as stored

- [ ] **Step 1: Write the failing test**

In `tests/test_server.py`, extend `ServerTestCase.setUpClass`: add `paths.ECO_FOOTPRINTS` to the `cls._saved` tuple (eighth) and `tearDownClass` restore, set `paths.ECO_FOOTPRINTS = paths.DATA / "eco" / "footprints.geojson"` beside the EMIT line, and after the EMIT fixture write add an ECOSTRESS fixture and pairs:

```python
        def eco_feature(fid, ring, start, daynight):
            return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"id": fid, "start": start, "end": start, "daynight": daynight,
                                   "year": int(start[:4]), "orbit": 1}}

        paths.ECO_FOOTPRINTS.parent.mkdir(parents=True)
        paths.ECO_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": [
            eco_feature("eco-day", square(cls.lon, cls.lat, 3.0), "2025-07-20T17:59:30Z", "DAY"),
            eco_feature("eco-night", square(cls.lon, cls.lat, 3.0), "2025-07-21T05:00:00Z", "NIGHT"),
            eco_feature("eco-far", square(cls.lon + 20, cls.lat, 3.0), "2025-07-20T18:00:00Z", "DAY"),
        ]}))
        # Attach coincidence pairs to the EMIT fixture the way ./run.sh footprints would.
        from viz import coincidence
        emit_doc = json.loads(paths.EMIT_FOOTPRINTS.read_text())
        eco_doc = json.loads(paths.ECO_FOOTPRINTS.read_text())
        coincidence.pair(emit_doc["features"], eco_doc["features"])
        paths.EMIT_FOOTPRINTS.write_text(json.dumps(emit_doc))
```

Add a test class:

```python
class TestEcoRoutes(ServerTestCase):
    def test_eco_footprints_are_served(self):
        status, ctype, body = self.get("/api/eco/footprints.geojson")
        self.assertEqual(status, 200)
        self.assertIn("geo+json", ctype)
        self.assertEqual(len(json.loads(body)["features"]), 3)

    def test_catalog_reports_eco_fields(self):
        catalog = json.loads(self.get("/api/catalog")[2])
        self.assertEqual(catalog["eco_count"], 3)
        self.assertRegex(catalog["eco_fetched"], r"^\d{4}-\d{2}-\d{2}T")
        # near-new (2025-07-20T18:00:00Z) pairs with eco-day 30 s earlier.
        self.assertEqual(catalog["coincident_15min"], 1)

    def test_point_lists_covering_eco_swaths_newest_first(self):
        report = json.loads(self.get(self.point_url())[2])
        self.assertEqual([g["id"] for g in report["eco"]], ["eco-night", "eco-day"])
        self.assertEqual(report["eco"][0]["daynight"], "NIGHT")

    def test_emit_entries_carry_their_pairs(self):
        report = json.loads(self.get(self.point_url())[2])
        near_new = next(g for g in report["emit"] if g["id"] == "near-new")
        self.assertEqual(near_new["eco"][0]["id"], "eco-day")
        self.assertEqual(near_new["eco"][0]["dt"], -30)

    def test_missing_eco_file_gives_404_and_zero_fields(self):
        moved = paths.ECO_FOOTPRINTS.with_name("moved.geojson")
        paths.ECO_FOOTPRINTS.rename(moved)
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/eco/footprints.geojson")
            self.assertEqual(ctx.exception.code, 404)
            catalog = json.loads(self.get("/api/catalog")[2])
            self.assertEqual(catalog["eco_count"], 0)
            self.assertEqual(json.loads(self.get(self.point_url())[2])["eco"], [])
        finally:
            moved.rename(paths.ECO_FOOTPRINTS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestEcoRoutes -v`
Expected: FAIL/ERROR on every test.

- [ ] **Step 3: Write minimal implementation**

In `viz/tileserver.py`:

1. Import: `from viz import coincidence, color, emit, naming, paths, rasters`.

2. In `point_report`, after the `granules = ...` line:

```python
    eco_index = emit.index_for(paths.ECO_FOOTPRINTS)
    eco_swaths = eco_index.covering(lon, lat) if eco_index else []
```

and add `"eco": eco_swaths,` to the returned dict.

3. In the `/api/catalog` branch, after the `emit_fetched` line:

```python
                eco_index = emit.index_for(paths.ECO_FOOTPRINTS)
                catalog["eco_count"] = eco_index.count if eco_index else 0
                catalog["eco_fetched"] = eco_index.fetched if eco_index else None
                catalog["coincident_15min"] = index.count_where(
                    lambda p: bool(p.get("eco")) and abs(p["eco"][0]["dt"]) <= coincidence.SAME_PASS
                ) if index else 0
```

4. Add a route after the EMIT footprints route:

```python
            if route == "/api/eco/footprints.geojson":
                if not paths.ECO_FOOTPRINTS.is_file():
                    return self._fail(HTTPStatus.NOT_FOUND, "no ECOSTRESS footprints; run ./run.sh footprints")
                return self._send(paths.ECO_FOOTPRINTS.read_bytes(), CONTENT_TYPES[".geojson"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server -v`
Expected: PASS. `./run.sh test` green. Then restart the server and probe:

```bash
pkill -f 'python3 -m viz[.]tileserver'; sleep 1; nohup ./run.sh serve > /tmp/serve.log 2>&1 &
sleep 3
curl -s http://127.0.0.1:8000/api/catalog | python3 -c "import json,sys; c=json.load(sys.stdin); print(c['eco_count'], c['eco_fetched'], 'coincident_15min', c['coincident_15min'])"
curl -s 'http://127.0.0.1:8000/api/point?lon=-93.62&lat=42.03&crop=corn&year=2025&cdl_year=2025&week=30' | python3 -c "
import json,sys; r=json.load(sys.stdin); print(len(r['eco']), 'eco swaths;', r['eco'][0]['start'] if r['eco'] else None)
paired=[g for g in r['emit'] if g.get('eco')]; print(len(paired), 'of', len(r['emit']), 'EMIT with pairs; example dt', paired[0]['eco'][0]['dt'] if paired else None)"
```

Expected: `eco_count` near 75,000 with a timestamp, a `coincident_15min` count in the thousands, ECOSTRESS swaths over Ames, and EMIT entries with `dt` values of seconds for same-pass pairs.

- [ ] **Step 5: Commit**

```bash
git add viz/tileserver.py tests/test_server.py
git commit -m "Serve ECOSTRESS footprints and report coincidence in the catalog and point query"
```

---

### Task 5: Interface

**Files:**
- Modify: `viz/web/app.js`, `viz/web/index.html`, `viz/web/style.css`
- Test: `tests/test_server.py` (static assertions)

**Interfaces:**
- Consumes: `/api/eco/footprints.geojson`; catalog `eco_count`, `coincident_15min`; `/api/point` `eco`; EMIT feature `eco` pairs
- Produces: the interface

- [ ] **Step 1: Write the failing test**

Add to `TestInterfaceAssets`:

```python
    def test_ecostress_layer_and_coincidence_controls(self):
        _, _, index = self.get("/")
        html = index.decode()
        for ident in ('id="eco"', 'name="ecoDay"', 'id="coincide"', 'id="coincideWindow"',
                      'id="coincideOnly"', 'id="ecoLegend"'):
            self.assertIn(ident, html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("/api/eco/footprints.geojson", text)
        self.assertIn("COINCIDE_STEPS", text)
        self.assertIn("[1, 5, 15, 30, 60, 120, 360, 720, 1440]", text)
        self.assertIn('dashArray: "4 4"', text)
        self.assertIn("fillOpacity", text[text.index("function emitStyleFor"):text.index("function loadEmit")])
        self.assertIn("ECOSTRESS swaths covering this point", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestInterfaceAssets -v`
Expected: FAIL on the new test.

- [ ] **Step 3: index.html**

Inside `#emitControls`, after the `emitOnlyWindow` label and before `#emitLegend`:

```html
      <label class="inline"><input type="checkbox" id="coincide"> Mark ECOSTRESS coincidence</label>
      <label>Coincidence window <output id="coincideWindowOut">15 min</output>
        <input type="range" id="coincideWindow" min="0" max="8" step="1" value="2">
      </label>
      <label class="inline"><input type="checkbox" id="coincideOnly"> Only coincident scenes</label>
```

After the closing `</div>` of `#emitControls`:

```html
    <label class="inline"><input type="checkbox" id="eco"> ECOSTRESS swaths</label>
    <div class="sub" id="ecoControls">
      <div class="inline">
        <label class="inline"><input type="radio" name="ecoDay" value="DAY" checked> Day</label>
        <label class="inline"><input type="radio" name="ecoDay" value="NIGHT"> Night</label>
        <label class="inline"><input type="radio" name="ecoDay" value="BOTH"> Both</label>
      </div>
      <div id="ecoLegend"></div>
    </div>
```

- [ ] **Step 4: style.css**

Append:

```css
/* ECOSTRESS swaths */
#ecoControls { display: grid; gap: 7px; padding-left: 22px; }
#ecoControls.disabled { opacity: 0.5; }
#ecoLegend { font-size: 11px; color: var(--muted); }
#ecoLegend span::before {
  content: ""; display: inline-block; width: 14px; height: 0; margin-right: 4px;
  border-top: 2px dashed #5b6770; vertical-align: middle;
}
.leaflet-eco-pane { cursor: default; }
.readout-popup .emit-list .eco-tag { color: var(--muted); font-size: 11px; }
```

- [ ] **Step 5: app.js**

1. In the `var state = {` literal, after `emitOnlyWindow: false` (add a comma to that line):

```javascript
    coincide: false,
    coincideStep: 2,
    coincideOnly: false,
    eco: false,
    ecoDay: "DAY"
```

2. After the `var emitLoaded = false;` line:

```javascript
  map.createPane("eco");
  map.getPane("eco").style.zIndex = 452;   // below EMIT, above CPC
  var ecoRenderer = L.canvas({ pane: "eco" });
  var ECO_COLOUR = "#5b6770";
  var COINCIDE_STEPS = [1, 5, 15, 30, 60, 120, 360, 720, 1440];   // minutes
  var ecoLayer = null;
  var ecoLoaded = false;

  function coincideSeconds() { return COINCIDE_STEPS[state.coincideStep] * 60; }

  function formatDt(seconds) {
    var sign = seconds < 0 ? "−" : "+";
    var s = Math.abs(seconds);
    if (s < 60) { return sign + s + " s"; }
    if (s < 3600) { return sign + Math.round(s / 60) + " m"; }
    return sign + Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " m";
  }

  function stepLabel(step) {
    var m = COINCIDE_STEPS[step];
    return m < 60 ? m + " min" : (m / 60) + " h";
  }
```

3. Replace `emitStyleFor` in full:

```javascript
  function emitStyleFor(bounds) {
    var maxDt = coincideSeconds();
    return function (feature) {
      var p = feature.properties;
      var inWindow = !!(bounds && p._t >= bounds[0] && p._t <= bounds[1]);
      var nearest = p.eco && p.eco.length ? p.eco[0].dt : null;
      var coincident = state.coincide && nearest !== null && Math.abs(nearest) <= maxDt;
      var hidden = (p.cloud !== null && p.cloud > state.emitCloud) ||
                   (state.emitOnlyWindow && bounds && !inWindow) ||
                   (state.coincide && state.coincideOnly && !coincident);
      var yearColour = EMIT_YEAR_COLOURS[p.year] || "#666";
      return {
        stroke: !hidden, interactive: !hidden,
        fill: coincident, fillColor: yearColour, fillOpacity: coincident ? 0.25 : 0,
        color: inWindow ? EMIT_HIGHLIGHT : yearColour,
        weight: inWindow ? 2.5 : 1,
        opacity: inWindow ? 0.95 : (bounds ? 0.35 : 0.8)
      };
    };
  }
```

4. After `drawEmitLegend`, add the ECOSTRESS functions:

```javascript
  function ecoStyleFor(bounds) {
    return function (feature) {
      var p = feature.properties;
      var inWindow = !!(bounds && p._t >= bounds[0] && p._t <= bounds[1]);
      var hidden = state.ecoDay !== "BOTH" && p.daynight !== state.ecoDay;
      return {
        stroke: !hidden, interactive: !hidden, fill: false,
        color: inWindow ? EMIT_HIGHLIGHT : ECO_COLOUR,
        dashArray: "4 4",
        weight: inWindow ? 2.5 : 1,
        opacity: inWindow ? 0.95 : (bounds ? 0.35 : 0.7)
      };
    };
  }

  function loadEco() {
    if (ecoLoaded || !(state.catalog.eco_count > 0)) { return; }
    ecoLoaded = true;
    fetch("/api/eco/footprints.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      ecoLayer = L.geoJSON(geo, {
        pane: "eco", renderer: ecoRenderer, style: ecoStyleFor(emitWindowBounds()),
        onEachFeature: function (f, layer) {
          var p = f.properties;
          p._t = Date.parse(p.start);
          layer.bindTooltip(p.start.slice(0, 10) + " " + p.start.slice(11, 16) + " UTC · " +
                            (p.daynight || "?").toLowerCase() + " · bounding box",
                            { sticky: true, className: "emit-tip" });
        }
      });
      syncEco();
    }).catch(function () { ecoLoaded = false; });
  }

  function drawEcoLegend() {
    el("ecoLegend").innerHTML = "<span>ECOSTRESS swath, bounding box (≈550 km), not the true outline</span>";
  }

  function syncEco() {
    var available = state.catalog.eco_count > 0;
    var box = el("eco");
    box.disabled = !available;
    box.parentNode.title = available ? "" : "No ECOSTRESS footprints; run ./run.sh footprints";
    el("ecoControls").classList.toggle("disabled", !(available && state.eco));
    Array.prototype.forEach.call(document.getElementsByName("ecoDay"), function (radio) {
      radio.disabled = !(available && state.eco);
    });
    if (!state.eco && ecoLayer && map.hasLayer(ecoLayer)) { map.removeLayer(ecoLayer); }
    if (state.eco) {
      if (!ecoLayer) { loadEco(); return; }
      if (!map.hasLayer(ecoLayer)) { ecoLayer.addTo(map); }
      ecoLayer.setStyle(ecoStyleFor(emitWindowBounds()));
    }
  }
```

5. In `syncEmit`, after the `emitOnlyWindow` block and before the `if (!state.emit && emitLayer ...)` line:

```javascript
    var coincideOk = available && state.emit && state.catalog.eco_count > 0;
    el("coincide").disabled = !coincideOk;
    el("coincideWindow").disabled = el("coincideOnly").disabled = !(coincideOk && state.coincide);
    if (!coincideOk && state.coincide) { state.coincide = false; el("coincide").checked = false; }
    if (!state.coincide && state.coincideOnly) { state.coincideOnly = false; el("coincideOnly").checked = false; }
```

6. In `drawEmitLegend`, append one more legend entry after the "within window" span:

```javascript
      + '<span class="hl" style="--swatch:#8b45d9">filled = ECOSTRESS coincident</span>'
```

(adjust the concatenation so it remains one expression).

7. In `refresh()`, after `syncEmit();` add `syncEco();`.

8. In `wire()`, after the `emitWindow` handler:

```javascript
    el("coincide").addEventListener("change", function (e) { state.coincide = e.target.checked; syncEmit(); });
    el("coincideWindow").addEventListener("input", function (e) {
      state.coincideStep = Number(e.target.value);
      el("coincideWindowOut").textContent = stepLabel(state.coincideStep);
      syncEmit();
    });
    el("coincideOnly").addEventListener("change", function (e) { state.coincideOnly = e.target.checked; syncEmit(); });
    el("eco").addEventListener("change", function (e) { state.eco = e.target.checked; syncEco(); });
    Array.prototype.forEach.call(document.getElementsByName("ecoDay"), function (radio) {
      radio.addEventListener("change", function (e) { if (e.target.checked) { state.ecoDay = e.target.value; syncEco(); } });
    });
```

9. In `showReadout`, inside the EMIT row builder, after the `browse`/`data` anchors and before the star, add the coincidence tag:

```javascript
               (g.eco && g.eco.length && state.coincide && Math.abs(g.eco[0].dt) <= coincideSeconds()
                 ? ' <span class="eco-tag">ECOSTRESS ' + formatDt(g.eco[0].dt) + "</span>" : "") +
```

and after the EMIT block (before `return html;`):

```javascript
    var swaths = report.eco || [];
    html += '<p class="section">ECOSTRESS swaths covering this point: ' + swaths.length + "</p>";
    if (swaths.length) {
      var centreE = report.week_sunday ? Date.parse(report.week_sunday + "T00:00:00Z") : null;
      var sortedE = swaths.slice().sort(function (a, b) {
        if (centreE === null) { return Date.parse(b.start) - Date.parse(a.start); }
        return Math.abs(Date.parse(a.start) - centreE) - Math.abs(Date.parse(b.start) - centreE);
      });
      var shownE = sortedE.slice(0, 10);
      html += '<ul class="emit-list">' + shownE.map(function (s) {
        return '<li><span class="when">' + s.start.slice(0, 10) + " " + s.start.slice(11, 16) + "</span>" +
               "<span>" + (s.daynight || "?").toLowerCase() + "</span></li>";
      }).join("") + "</ul>";
      if (swaths.length > shownE.length) {
        html += '<p class="note">and ' + (swaths.length - shownE.length) + " more</p>";
      }
    }
```

10. In the catalog `fetch(...)` block, change `drawEmitLegend(); syncEmit();` to `drawEmitLegend(); drawEcoLegend(); syncEmit(); syncEco();`.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && node --check viz/web/app.js && PYTHONNOUSERSITE=1 python3 -m unittest discover -s tests -t . 2>&1 | tail -1`
Expected: `OK`. No server restart needed.

- [ ] **Step 7: Browser check (for the user)**

1. Under the EMIT controls: "Mark ECOSTRESS coincidence" (unticked), a "Coincidence window" slider reading 15 min, and "Only coincident scenes", all dimmed until the EMIT layer is on. Below the EMIT block: "ECOSTRESS swaths" (unticked) with Day / Night / Both and the bounding-box caption.
2. Tick EMIT, then "Mark ECOSTRESS coincidence": many EMIT footprints gain a translucent fill in their year colour; the rest stay outline-only. Widen the window to 24 h: more fill. Narrow to 1 min: fewer.
3. Tick "Only coincident scenes": unfilled footprints vanish.
4. With week 30 of 2025 and a 7-day window: black-outlined AND filled footprints are the joint acquisitions near that week.
5. Tick "ECOSTRESS swaths": large dashed grey rectangles appear (Day only). Switch to Night, then Both. Hover one: date, time, day/night, "bounding box".
6. Click in Iowa: EMIT rows now carry "ECOSTRESS +41 s"-style tags where a same-pass pair exists inside the window, and a new block lists ECOSTRESS swaths covering the point with date, time, and day/night.
7. Untick everything: layers vanish, controls dim.

- [ ] **Step 8: Commit**

```bash
git add viz/web/app.js viz/web/index.html viz/web/style.css tests/test_server.py
git commit -m "Draw ECOSTRESS swath boxes and mark EMIT-ECOSTRESS coincidence"
```

---

## Self-Review

**Spec coverage.** §2 informs Task 2's constants and the caption text. §3 decisions map to Task 2 (boxes, years), Task 3 (24 h precompute, 15 min default in `SAME_PASS`), Task 5 (day/night control, slider steps, defaults). §4.1 is Tasks 1 and 2; §4.2 is Task 3; §4.3 is Task 4; §4.4 and §4.5 are Task 5; §4.6 is Task 5 step 9. §5 testing is distributed. §6 out of scope honoured.

**Placeholder scan.** Every step carries runnable content.

**Type consistency.** `cmr.link` replaces `fetch_emit._link`, same signature. `paths.ECO_FOOTPRINTS` defined in Task 2, used in Tasks 3–4. `coincidence.pair` writes `eco` pairs with keys `id`, `start`, `daynight`, `dt`, which `emitStyleFor` and `showReadout` read (`p.eco[0].dt`). `FootprintIndex.count_where` defined in Task 3, used in Task 4. The catalog keys `eco_count`, `eco_fetched`, `coincident_15min` are written in Task 4 and read in Task 5's `syncEco`/`syncEmit`. The ECOSTRESS route string is identical in Task 4 and Task 5.
