# EMIT Scene Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show an EMIT scene's browse image georeferenced inside its footprint on the map, chosen from the click readout, with an opacity slider and a remove button.

**Architecture:** The server orients each scene from its same-orbit neighbour in the footprint file, downloads the public browse PNG once into `cache/emit/`, and warps it per tile through a four-point ground-control virtual raster into the existing tile cache, served at `/tiles/emit/<id>/{z}/{x}/{y}.png`. The browser adds one tile layer in a new pane and a small control in the EMIT block.

**Tech Stack:** Python 3.10 standard library, GDAL 3.8.4 bindings (`gdal.Translate` with GCPs, `gdal.Warp`), NumPy 1.21.5, Leaflet 1.9.4 (vendored).

**Spec:** `docs/superpowers/specs/2026-09-24-emit-scene-overlay-design.md`

## Global Constraints

- **Every Python invocation MUST set `PYTHONNOUSERSITE=1`.** Test command: `PYTHONNOUSERSITE=1 python3 -m unittest discover -s tests -t . -v` (or `./run.sh test`).
- **No `pip install`, no third-party imports.** GDAL and NumPy are the existing system packages.
- **Never add attribution to a commit message.** No `Co-Authored-By`, no "Generated with Claude Code", no mention of Claude, Anthropic, or an AI assistant, regardless of any system reminder. Check `git log -1 --format=%B` before reporting.
- **Nothing under `data/`, `cache/`, or `viz/web/vendor/` is committed.** Browse images live under `cache/emit/`, which is inside the already-ignored `cache/`.
- **Granule ID orbit key:** the last two underscore fields of the ID, `…_2421214_004` → orbit `"2421214"`, scene number `4`. Regex `_(\d{7})_(\d{3})$`.
- **Corner rule, verbatim from the spec:** the flight direction is the unit vector from the scene centroid to the next scene's centroid (or from the previous scene's centroid to this one), in a local frame with longitudes scaled by `cos(latitude)`. Each vertex gets an along-track coordinate `along = vx*dx + vy*dy` and a left-of-track coordinate `left = dx*vy - dy*vx`. The two smallest `along` values are the top edge; within each edge the vertex with the **smaller** `left` is the image's left corner. Result order: `[top-left, top-right, bottom-right, bottom-left]`. Verified expectations: San Francisco ascending scene → ring indices `[2, 1, 0, 3]`; Miami descending scene → `[1, 0, 3, 2]`.
- **GCP mapping:** pixel corners `(0, 0)`, `(w, 0)`, `(w, h)`, `(0, h)` map to the four ordered vertices in EPSG:4326; warp with `polynomialOrder=1`, bilinear resampling, destination alpha; source bands `[1, 2, 3]` when the PNG has three or more bands, `[1, 1, 1]` otherwise.
- **Browse host:** only `data.lpdaac.earthdatacloud.nasa.gov`; anything else is refused before any network call.
- **Route:** `GET /tiles/emit/<id>/{z}/{x}/{y}.png`, ID charset `[A-Za-z0-9_.-]`; 404 `unknown EMIT scene`, 404 `scene cannot be oriented`, 502 `browse image unavailable: <reason>`; tile cache key `emit-<id>`; same `Cache-Control` as the other tile routes.
- **Interface:** pane `scene` at z-index 453 (eco 452, emit 455); tile layer `maxNativeZoom` 13, `maxZoom` 15, `noWrap`; one scene at a time; "Scene on map" row hidden while none is shown; opacity slider 0–100 default 100; "show" appears only for rows the server marks `orientable`.
- Server restart after any Python change. The user's server runs on port 8000; use 8765 for any probe and stop it.

## Review Focus

1. A scene ID containing `..` or a slash reaching the tile route must be rejected by the route pattern, never used to build a path (test in Task 3).
2. A footprint whose `browse` property is null must yield 502, not a crash (test in Task 3).
3. A stale `.part` file from an interrupted download must never be served; only the final file counts as cached (test in Task 2).
4. A tile far from the scene must be answered from the shared transparent tile without a warp (test in Task 2).
5. Choosing a second scene must remove the first layer before adding the new one, so two overlays never stack (static test in Task 4).

## File Structure

| Path | Responsibility |
| --- | --- |
| `viz/emit.py` | `orbit_key(scene_id)`, `FootprintIndex.scene(id)`, `.scene_bbox(id)`, `.neighbour(id)`, `scene_corners(index, id)` |
| `viz/paths.py` | `SCENE_CACHE = CACHE / "emit"` |
| `viz/scene.py` | `BrowseError`, `browse_path`, `download`, `ensure_browse`, `gcp_source`, `render_tile`, `transparent_tile` |
| `viz/tileserver.py` | `TILE_EMIT_RE`, `_handle_emit_tile`, `orientable` and `bbox` on point-route EMIT entries |
| `viz/web/index.html`, `app.js`, `style.css` | Scene pane and layer, "show" action, "Scene on map" control |
| `tests/test_emit.py`, `tests/test_scene.py`, `tests/test_server.py` | Tests |

---

### Task 1: Orbit neighbours and the corner rule

**Files:**
- Modify: `viz/emit.py`
- Test: `tests/test_emit.py`

**Interfaces:**
- Consumes: the existing `FootprintIndex` (`_items` of `(bbox, ring, props)`).
- Produces: `emit.orbit_key(scene_id) -> (orbit: str, number: int) | None`; `FootprintIndex.scene(scene_id) -> props | None`; `FootprintIndex.scene_bbox(scene_id) -> (minlon, minlat, maxlon, maxlat) | None`; `FootprintIndex.neighbour(scene_id) -> (props, sign) | None` where `sign` is `+1` for the next scene and `-1` for the previous; `emit.scene_corners(index, scene_id) -> [[lon, lat] * 4] | None` in image order.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emit.py` (add `import json, shutil, tempfile` and `from pathlib import Path` at the top if they are not there):

```python
SF_RING = [[-121.662, 38.761], [-122.56, 38.167], [-122.119, 37.501], [-121.221, 38.095], [-121.662, 38.761]]
SF_NEXT = [[-121.0, 39.3], [-121.9, 38.7], [-121.46, 38.04], [-120.56, 38.63], [-121.0, 39.3]]      # further north-east
MIA_RING = [[-79.9, 26.04], [-80.5, 25.5], [-79.95, 24.9], [-79.35, 25.44], [-79.9, 26.04]]
MIA_PREV = [[-80.5, 26.7], [-81.1, 26.16], [-80.55, 25.56], [-79.95, 26.1], [-80.5, 26.7]]           # further north-west


def scene_feature(fid, ring, start="2024-07-30T20:39:50Z"):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": fid, "start": start, "end": start, "cloud": 10.0, "year": 2024,
                           "browse": "https://data.lpdaac.earthdatacloud.nasa.gov/x/" + fid + ".png", "data": None}}


class TestOrbitKey(unittest.TestCase):
    def test_parses_orbit_and_scene_number(self):
        self.assertEqual(emit.orbit_key("EMIT_L2A_RFL_001_20240730T203950_2421214_004"), ("2421214", 4))

    def test_malformed_is_none(self):
        self.assertIsNone(emit.orbit_key("near-new"))
        self.assertIsNone(emit.orbit_key("EMIT_L2A_RFL_001_20240730T203950_2421214"))
        self.assertIsNone(emit.orbit_key(None))


class TestSceneCorners(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        path = self.tmp / "footprints.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": [
            scene_feature("EMIT_L2A_RFL_001_20240730T203950_2421214_004", SF_RING),
            scene_feature("EMIT_L2A_RFL_001_20240730T204002_2421214_005", SF_NEXT),
            scene_feature("EMIT_L2A_RFL_001_20240622T165755_2417411_055", MIA_RING),
            scene_feature("EMIT_L2A_RFL_001_20240622T165743_2417411_054", MIA_PREV),
            scene_feature("EMIT_L2A_RFL_001_20240101T000000_2400101_001", SF_RING),   # no neighbour
            scene_feature("near-new", SF_RING),                                        # no orbit key
        ]}))
        self.index = emit.FootprintIndex(path)

    def test_scene_lookup_and_bbox(self):
        self.assertEqual(self.index.scene("near-new")["id"], "near-new")
        self.assertIsNone(self.index.scene("nope"))
        self.assertEqual(self.index.scene_bbox("EMIT_L2A_RFL_001_20240622T165755_2417411_055"),
                         (-80.5, 24.9, -79.35, 26.04))
        self.assertIsNone(self.index.scene_bbox("nope"))

    def test_neighbour_prefers_next_then_previous(self):
        props, sign = self.index.neighbour("EMIT_L2A_RFL_001_20240730T203950_2421214_004")
        self.assertEqual((props["id"][-3:], sign), ("005", 1))
        props, sign = self.index.neighbour("EMIT_L2A_RFL_001_20240622T165755_2417411_055")
        self.assertEqual((props["id"][-3:], sign), ("054", -1))
        self.assertIsNone(self.index.neighbour("EMIT_L2A_RFL_001_20240101T000000_2400101_001"))
        self.assertIsNone(self.index.neighbour("near-new"))

    def test_ascending_pass_corner_order(self):
        corners = emit.scene_corners(self.index, "EMIT_L2A_RFL_001_20240730T203950_2421214_004")
        self.assertEqual(corners, [SF_RING[2], SF_RING[1], SF_RING[0], SF_RING[3]])

    def test_descending_pass_corner_order(self):
        corners = emit.scene_corners(self.index, "EMIT_L2A_RFL_001_20240622T165755_2417411_055")
        self.assertEqual(corners, [MIA_RING[1], MIA_RING[0], MIA_RING[3], MIA_RING[2]])

    def test_unorientable_scenes_are_none(self):
        self.assertIsNone(emit.scene_corners(self.index, "EMIT_L2A_RFL_001_20240101T000000_2400101_001"))
        self.assertIsNone(emit.scene_corners(self.index, "near-new"))
        self.assertIsNone(emit.scene_corners(self.index, "nope"))
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_emit -v`
Expected: FAIL with `AttributeError: module 'viz.emit' has no attribute 'orbit_key'`.

- [ ] **Step 3: Implement**

In `viz/emit.py`, add `import math` and `import re` to the imports and, after `point_in_ring`:

```python
_ORBIT_RE = re.compile(r"_(\d{7})_(\d{3})$")


def orbit_key(scene_id):
    """('2421214', 4) from '…_2421214_004'; None when the ID lacks the orbit fields."""
    match = _ORBIT_RE.search(scene_id or "")
    return (match.group(1), int(match.group(2))) if match else None


def ring_centroid(ring):
    """Mean of the ring's vertices, ignoring a closing duplicate."""
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
```

In `FootprintIndex.__init__`, after `self._items = []` add `self._by_id = {}` and `self._by_orbit = {}`, and inside the feature loop after `self._items.append(...)`:

```python
            scene_id = feature["properties"].get("id")
            self._by_id[scene_id] = (bbox, ring, feature["properties"])
            key = orbit_key(scene_id)
            if key:
                self._by_orbit[key] = feature["properties"]
```

Add these methods to `FootprintIndex`:

```python
    def scene(self, scene_id):
        """Properties of one footprint by granule ID, or None."""
        item = self._by_id.get(scene_id)
        return item[2] if item else None

    def scene_bbox(self, scene_id):
        """(minlon, minlat, maxlon, maxlat) of one footprint, or None."""
        item = self._by_id.get(scene_id)
        return item[0] if item else None

    def scene_ring(self, scene_id):
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
```

After the class, add:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_emit -v`
Expected: all PASS, including the two verified corner orders.

- [ ] **Step 5: Commit**

```bash
git add viz/emit.py tests/test_emit.py
git commit -m "Orient EMIT scenes from their same-orbit neighbour"
```

---

### Task 2: Browse download, GCP raster, and tile render

**Files:**
- Modify: `viz/paths.py`
- Create: `viz/scene.py`
- Create: `tests/test_scene.py`

**Interfaces:**
- Consumes: `rasters.encode_png(rgba)`, `gridmath.tile_bounds(z, x, y)`, `gridmath.TILE_SIZE`, `paths.SCENE_CACHE`, `paths.TILE_CACHE`.
- Produces: `scene.BrowseError(Exception)`; `scene.BROWSE_HOST = "data.lpdaac.earthdatacloud.nasa.gov"`; `scene.browse_path(scene_id) -> Path`; `scene.download(url, dest)` (urllib, 60 s timeout, raises `BrowseError`); `scene.ensure_browse(scene_id, url, fetch=download) -> Path`; `scene.render_tile(scene_id, corners, z, x, y) -> bytes` (PNG); `scene.transparent_tile() -> bytes`; `scene.tile_lonlat_bounds(z, x, y)`.

- [ ] **Step 1: Add the cache path**

Append to `viz/paths.py` after `TILE_CACHE`:

```python
SCENE_CACHE = CACHE / "emit"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_scene.py`:

```python
import math
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal

from viz import gridmath, paths, scene

gdal.UseExceptions()

# A one-degree square: top-left (-100, 40), top-right (-99, 40), bottom-right (-99, 39), bottom-left (-100, 39).
CORNERS = [[-100.0, 40.0], [-99.0, 40.0], [-99.0, 39.0], [-100.0, 39.0]]


def write_png(path, bands=3):
    """A 4x4 image, mid-grey everywhere except a pure red top-left pixel."""
    ds = gdal.GetDriverByName("MEM").Create("", 4, 4, bands, gdal.GDT_Byte)
    for index in range(bands):
        data = np.full((4, 4), 128, dtype=np.uint8)
        if bands >= 3:
            data[0, 0] = 255 if index == 0 else 0
        else:
            data[0, 0] = 255
        ds.GetRasterBand(index + 1).WriteArray(data)
    gdal.GetDriverByName("PNG").CreateCopy(str(path), ds)


def pixel_of(lon, lat, z, x, y):
    """Pixel (col, row) of a lon/lat inside an XYZ tile."""
    n = 2 ** z
    px = ((lon + 180.0) / 360.0 * n - x) * gridmath.TILE_SIZE
    py = ((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n - y) * gridmath.TILE_SIZE
    return int(px), int(py)


def decode(blob):
    name = "/vsimem/decode_test.png"
    gdal.FileFromMemBuffer(name, blob)
    try:
        ds = gdal.Open(name)
        return ds.ReadAsArray()          # (bands, rows, cols)
    finally:
        gdal.Unlink(name)


class SceneTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.saved = (paths.SCENE_CACHE, paths.TILE_CACHE)
        paths.SCENE_CACHE = self.tmp / "emit"
        paths.TILE_CACHE = self.tmp / "tiles"
        self.addCleanup(self._restore)
        scene.forget_all()

    def _restore(self):
        paths.SCENE_CACHE, paths.TILE_CACHE = self.saved
        scene.forget_all()


class TestEnsureBrowse(SceneTestCase):
    def test_downloads_once_through_a_part_file(self):
        calls = []

        def fake(url, dest):
            calls.append((url, str(dest)))
            write_png(dest)

        url = "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-public/x/S1.png"
        path = scene.ensure_browse("S1", url, fetch=fake)
        self.assertEqual(path, paths.SCENE_CACHE / "S1.png")
        self.assertTrue(path.is_file())
        self.assertTrue(calls[0][1].endswith(".part"))
        self.assertFalse(list(paths.SCENE_CACHE.glob("*.part")))
        scene.ensure_browse("S1", url, fetch=fake)
        self.assertEqual(len(calls), 1)

    def test_refuses_other_hosts_and_missing_urls_before_fetching(self):
        def fake(url, dest):
            raise AssertionError("must not be called")

        with self.assertRaises(scene.BrowseError):
            scene.ensure_browse("S2", "https://example.com/x.png", fetch=fake)
        with self.assertRaises(scene.BrowseError):
            scene.ensure_browse("S3", None, fetch=fake)

    def test_stale_part_file_is_not_trusted(self):
        paths.SCENE_CACHE.mkdir(parents=True)
        (paths.SCENE_CACHE / "S4.png.part").write_bytes(b"junk")
        calls = []

        def fake(url, dest):
            calls.append(url)
            write_png(dest)

        scene.ensure_browse("S4", "https://data.lpdaac.earthdatacloud.nasa.gov/x/S4.png", fetch=fake)
        self.assertEqual(len(calls), 1)
        self.assertTrue((paths.SCENE_CACHE / "S4.png").is_file())

    def test_fetch_failure_is_a_browse_error_and_leaves_no_file(self):
        def fake(url, dest):
            raise OSError("timed out")

        with self.assertRaises(scene.BrowseError):
            scene.ensure_browse("S5", "https://data.lpdaac.earthdatacloud.nasa.gov/x/S5.png", fetch=fake)
        self.assertFalse((paths.SCENE_CACHE / "S5.png").exists())


class TestRenderTile(SceneTestCase):
    def setUp(self):
        super().setUp()
        paths.SCENE_CACHE.mkdir(parents=True)
        write_png(scene.browse_path("S1"))
        write_png(scene.browse_path("G1"), bands=1)

    def test_corner_pixel_lands_at_its_vertex_and_outside_is_transparent(self):
        z = 8
        x, y = gridmath.lonlat_to_tile(-99.9, 39.9, z)
        rgba = decode(scene.render_tile("S1", CORNERS, z, x, y))
        self.assertEqual(rgba.shape[0], 4)
        col, row = pixel_of(-99.9, 39.9, z, x, y)          # inside the top-left source pixel
        self.assertGreater(rgba[0, row, col], 200)
        self.assertLess(rgba[1, row, col], 60)
        self.assertEqual(rgba[3, row, col], 255)
        col, row = pixel_of(-99.1, 39.1, z, x, y)          # bottom-right source pixel: grey
        if 0 <= col < 256 and 0 <= row < 256:
            self.assertTrue(100 <= rgba[0, row, col] <= 160)
        col, row = pixel_of(-100.4, 40.4, z, x, y)         # outside the quad
        if 0 <= col < 256 and 0 <= row < 256:
            self.assertEqual(rgba[3, row, col], 0)

    def test_single_band_source_renders_grey(self):
        z = 8
        x, y = gridmath.lonlat_to_tile(-99.5, 39.5, z)
        rgba = decode(scene.render_tile("G1", CORNERS, z, x, y))
        col, row = pixel_of(-99.5, 39.5, z, x, y)
        self.assertEqual(int(rgba[0, row, col]), int(rgba[1, row, col]))
        self.assertEqual(int(rgba[1, row, col]), int(rgba[2, row, col]))

    def test_far_tile_is_the_shared_transparent_tile_and_is_cached(self):
        blob = scene.render_tile("S1", CORNERS, 8, 10, 10)
        self.assertEqual(blob, scene.transparent_tile())
        self.assertFalse((paths.TILE_CACHE / "emit-S1").exists())
        near = scene.render_tile("S1", CORNERS, 8, *gridmath.lonlat_to_tile(-99.5, 39.5, 8))
        self.assertNotEqual(near, blob)
        self.assertTrue(list((paths.TILE_CACHE / "emit-S1").rglob("*.png")))

    def test_tile_lonlat_bounds(self):
        west, south, east, north = scene.tile_lonlat_bounds(1, 0, 0)
        self.assertAlmostEqual(west, -180.0, places=6)
        self.assertAlmostEqual(east, 0.0, places=6)
        self.assertAlmostEqual(south, 0.0, places=6)
        self.assertAlmostEqual(north, 85.0511, places=3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_scene -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'viz.scene'`.

- [ ] **Step 4: Write the module**

Create `viz/scene.py`:

```python
"""EMIT scene overlays: the browse image cache and the georeferenced tile render.

A scene's browse PNG is in swath geometry, so it is placed with four ground
control points (its pixel corners onto the footprint vertices in image
order, see viz.emit.scene_corners) and warped per Web Mercator tile through
the same disk tile cache as the CDL and CPC layers. The download is the
project's only on-demand network access and is confined to the LP DAAC host.
"""

import math
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from viz import gridmath, paths, rasters

gdal.UseExceptions()

BROWSE_HOST = "data.lpdaac.earthdatacloud.nasa.gov"


class BrowseError(Exception):
    """The browse image could not be obtained."""


def browse_path(scene_id):
    return paths.SCENE_CACHE / f"{scene_id}.png"


def download(url, dest):
    """Fetch url into dest. Raises BrowseError on any network or HTTP failure."""
    request = urllib.request.Request(url, headers={"User-Agent": "usda-viz"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            Path(dest).write_bytes(response.read())
    except OSError as exc:
        raise BrowseError(str(exc)) from exc


_download_locks = {}
_download_locks_guard = threading.Lock()


def _lock_for(scene_id):
    with _download_locks_guard:
        return _download_locks.setdefault(scene_id, threading.Lock())


def ensure_browse(scene_id, url, fetch=download):
    """The cached browse file for a scene, downloading it once if absent.

    Only the final file counts as cached; a leftover .part from an interrupted
    download is overwritten. Concurrent callers for one scene download once.
    """
    target = browse_path(scene_id)
    if target.is_file():
        return target
    if not url or urllib.parse.urlparse(url).hostname != BROWSE_HOST:
        raise BrowseError(f"no browse image on {BROWSE_HOST} for {scene_id}")
    with _lock_for(scene_id):
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        try:
            fetch(url, part)
            os.replace(part, target)
        except BrowseError:
            part.unlink(missing_ok=True)
            raise
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise BrowseError(str(exc)) from exc
    return target


_sources = {}
_sources_guard = threading.Lock()


def gcp_source(scene_id, corners):
    """Name of an in-memory VRT placing the browse PNG by its four corners, built once per process."""
    with _sources_guard:
        name = _sources.get(scene_id)
    if name is not None:
        return name
    png = str(browse_path(scene_id))
    src = gdal.Open(png)
    width, height = src.RasterXSize, src.RasterYSize
    bands = [1, 2, 3] if src.RasterCount >= 3 else [1, 1, 1]
    pixels = [(0, 0), (width, 0), (width, height), (0, height)]
    gcps = [gdal.GCP(float(lon), float(lat), 0.0, float(px), float(py))
            for (lon, lat), (px, py) in zip(corners, pixels)]
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    name = f"/vsimem/scene_{scene_id}.vrt"
    gdal.Translate(name, src, format="VRT", bandList=bands, GCPs=gcps, outputSRS=srs.ExportToWkt())
    with _sources_guard:
        _sources[scene_id] = name
    return name


def forget_all():
    """Drop the per-process VRTs (tests swap the cache directory)."""
    with _sources_guard:
        for name in _sources.values():
            try:
                gdal.Unlink(name)
            except RuntimeError:
                pass
        _sources.clear()


def tile_lonlat_bounds(z, x, y):
    """(west, south, east, north) in degrees of one XYZ tile."""
    n = 2 ** z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west, south, east, north


_transparent = None


def transparent_tile():
    """PNG bytes of a fully transparent 256x256 tile, encoded once."""
    global _transparent
    if _transparent is None:
        _transparent = rasters.encode_png(np.zeros((gridmath.TILE_SIZE, gridmath.TILE_SIZE, 4), dtype=np.uint8))
    return _transparent


def _touches(corners, z, x, y):
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    west, south, east, north = tile_lonlat_bounds(z, x, y)
    return min(lons) <= east and west <= max(lons) and min(lats) <= north and south <= max(lats)


def render_tile(scene_id, corners, z, x, y):
    """One tile of the scene as PNG bytes, from the disk cache when present."""
    if not _touches(corners, z, x, y):
        return transparent_tile()
    target = paths.TILE_CACHE / f"emit-{scene_id}" / str(z) / str(x) / f"{y}.png"
    if target.is_file():
        return target.read_bytes()
    out = gdal.Warp(
        "", gcp_source(scene_id, corners),
        format="MEM", dstSRS="EPSG:3857", outputBounds=gridmath.tile_bounds(z, x, y),
        width=gridmath.TILE_SIZE, height=gridmath.TILE_SIZE,
        resampleAlg="bilinear", polynomialOrder=1, dstAlpha=True, multithread=True,
    )
    rgba = np.transpose(out.ReadAsArray(), (1, 2, 0))
    blob = rasters.encode_png(np.ascontiguousarray(rgba))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{threading.get_ident()}.part")
    tmp.write_bytes(blob)
    tmp.replace(target)
    return blob
```

- [ ] **Step 5: Run to verify pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_scene -v`
Expected: all PASS. If `gdal.Translate` rejects `bandList` together with `GCPs`, build the VRT in two steps (Translate with `bandList` to `/vsimem/scene_<id>_bands.vrt`, then Translate that with `GCPs`), and keep both names in `_sources` for `forget_all` to unlink.

- [ ] **Step 6: Commit**

```bash
git add viz/paths.py viz/scene.py tests/test_scene.py
git commit -m "Cache EMIT browse images and warp them into tiles by their corners"
```

---

### Task 3: The scene tile route and point-route flags

**Files:**
- Modify: `viz/tileserver.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `emit.index_for`, `FootprintIndex.scene/scene_bbox`, `emit.scene_corners`, `scene.ensure_browse`, `scene.render_tile`, `scene.BrowseError`.
- Produces: `GET /tiles/emit/<id>/{z}/{x}/{y}.png`; each `emit[i]` entry of `/api/point` gains `orientable: bool` and `bbox: [minlon, minlat, maxlon, maxlat]`.

- [ ] **Step 1: Extend the fixture**

In `ServerTestCase.setUpClass` of `tests/test_server.py`, add two features to the EMIT fixture list, after the `no-tile` feature, far from every other fixture so no existing assertion about covering scenes changes:

```python
            feature("EMIT_L2A_RFL_001_20240730T203950_2421214_004", square(cls.lon + 30, cls.lat, 0.4), "2024-07-30T20:39:50Z", 10.0),
            feature("EMIT_L2A_RFL_001_20240730T204002_2421214_005", square(cls.lon + 30.6, cls.lat + 0.5, 0.4), "2024-07-30T20:40:02Z", 10.0),
```

Change the two assertions that count EMIT features from `4` to `6` (`test_catalog_reports_emit_count_and_fetched` and any other `emit_count == 4`; find them with `grep -n '"emit_count"\], 4' tests/test_server.py`). Add `paths.SCENE_CACHE` to the `cls._saved` tuple and the restore tuple, set `paths.SCENE_CACHE = cls.tmp / "cache" / "emit"` beside `paths.TILE_CACHE`, and after the server thread starts add `from viz import scene as scene_module; scene_module.forget_all()`.

- [ ] **Step 2: Write the failing tests**

Append before `class TestInterfaceAssets`:

```python
class TestSceneTiles(ServerTestCase):
    SCENE = "EMIT_L2A_RFL_001_20240730T203950_2421214_004"

    def setUp(self):
        from viz import scene as scene_module
        from tests.test_scene import write_png
        self.calls = []

        def fake(url, dest):
            self.calls.append(url)
            write_png(dest)

        self._real = scene_module.download
        scene_module.download = fake
        # ensure_browse's default argument bound the real function at import time; rebind it.
        self._real_ensure = scene_module.ensure_browse
        scene_module.ensure_browse = lambda sid, url, fetch=fake: self._real_ensure(sid, url, fetch=fetch)
        self.addCleanup(self._restore)
        for path in list(paths.SCENE_CACHE.glob("*")) if paths.SCENE_CACHE.is_dir() else []:
            path.unlink()
        scene_module.forget_all()

    def _restore(self):
        from viz import scene as scene_module
        scene_module.download = self._real
        scene_module.ensure_browse = self._real_ensure

    def tile_url(self, scene_id, z=8):
        from viz import gridmath
        x, y = gridmath.lonlat_to_tile(self.lon + 30, self.lat, z)
        return f"/tiles/emit/{scene_id}/{z}/{x}/{y}.png"

    def test_serves_a_tile_and_downloads_the_browse_once(self):
        status, ctype, body = self.get(self.tile_url(self.SCENE))
        self.assertEqual((status, ctype), (200, "image/png"))
        self.assertEqual(_png_size(body), (256, 256))
        self.assertEqual(len(self.calls), 1)
        self.assertIn("2421214_004", self.calls[0])
        self.get(self.tile_url(self.SCENE))
        self.assertEqual(len(self.calls), 1)

    def test_unknown_scene_and_unorientable_scene_are_404(self):
        for scene_id, message in (("nope", "unknown EMIT scene"), ("near-new", "scene cannot be oriented")):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(self.tile_url(scene_id))
            self.assertEqual(ctx.exception.code, 404)
            self.assertIn(message, ctx.exception.read().decode())
        self.assertEqual(self.calls, [])

    def test_path_characters_in_the_id_never_reach_the_handler(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/emit/../x/8/1/1.png")
        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(self.calls, [])

    def test_failed_download_is_502(self):
        from viz import scene as scene_module

        def broken(url, dest):
            raise scene_module.BrowseError("timed out")

        scene_module.ensure_browse = lambda sid, url, fetch=broken: self._real_ensure(sid, url, fetch=fetch)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get(self.tile_url(self.SCENE))
        self.assertEqual(ctx.exception.code, 502)
        self.assertIn("browse image unavailable", ctx.exception.read().decode())

    def test_point_entries_carry_orientable_and_bbox(self):
        report = json.loads(self.get(self.point_url())[2])
        by_id = {g["id"]: g for g in report["emit"]}
        self.assertFalse(by_id["near-new"]["orientable"])
        self.assertEqual(len(by_id["near-new"]["bbox"]), 4)
        self.assertAlmostEqual(by_id["near-new"]["bbox"][0], self.lon - 0.5, places=6)
        far = json.loads(self.get(f"/api/point?lon={self.lon + 30:.6f}&lat={self.lat:.6f}&crop=corn&year=2024&cdl_year=2024")[2])
        self.assertTrue({g["id"]: g for g in far["emit"]}[self.SCENE]["orientable"])
```

- [ ] **Step 3: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestSceneTiles -v`
Expected: FAIL (404 `not found` on the tile route; `KeyError: 'orientable'`).

- [ ] **Step 4: Implement**

In `viz/tileserver.py`, extend the import to `from viz import coincidence, color, emit, hls, naming, paths, rasters, scene` and add after `TILE_CPC_RE`:

```python
TILE_EMIT_RE = re.compile(
    r"^/tiles/emit/(?P<id>[A-Za-z0-9_.-]+)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$"
)
```

In `point_report`, after `granules = [...]`, add:

```python
    for g in granules:
        g["orientable"] = emit.scene_corners(index, g.get("id")) is not None
        g["bbox"] = list(index.scene_bbox(g.get("id")) or ())
```

In `do_GET`, after the CPC tile match block:

```python
            match = TILE_EMIT_RE.match(route)
            if match:
                return self._handle_emit_tile(match)
```

Add the handler beside `_handle_cpc_tile`:

```python
    def _handle_emit_tile(self, match):
        scene_id = match.group("id")
        index = emit.index_for(paths.EMIT_FOOTPRINTS)
        props = index.scene(scene_id) if index else None
        if props is None:
            return self._fail(HTTPStatus.NOT_FOUND, "unknown EMIT scene")
        corners = emit.scene_corners(index, scene_id)
        if corners is None:
            return self._fail(HTTPStatus.NOT_FOUND, "scene cannot be oriented")
        try:
            scene.ensure_browse(scene_id, props.get("browse"))
        except scene.BrowseError as exc:
            return self._fail(HTTPStatus.BAD_GATEWAY, f"browse image unavailable: {exc}")
        z, x, y = (int(match.group(k)) for k in ("z", "x", "y"))
        self._send(scene.render_tile(scene_id, corners, z, x, y), "image/png", cache=True)
```

The route pattern's charset excludes `/`, so `/tiles/emit/../x/…` never matches and falls through to the generic 404.

- [ ] **Step 5: Run the suite**

Run: `./run.sh test 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add viz/tileserver.py tests/test_server.py
git commit -m "Serve EMIT scene overlay tiles and flag orientable scenes"
```

---

### Task 4: Scene layer, "show" action, and the "Scene on map" control

**Files:**
- Modify: `viz/web/index.html`
- Modify: `viz/web/style.css`
- Modify: `viz/web/app.js`
- Modify: `tests/test_server.py` (`TestInterfaceAssets`)

**Interfaces:**
- Consumes: `/tiles/emit/<id>/{z}/{x}/{y}.png?t=<token>`; `report.emit[i].orientable`, `.bbox`, `.start`, `.id`; the existing `wireFolds(popup)` hook and `readoutPopup`.
- Produces: `state.scene` (`{id, start}` or null), `state.sceneOpacity`; `showScene(id, start, bbox)`, `clearScene()`, `syncSceneControl()`, `wireSceneLinks(popup, report)`; elements `sceneRow`, `sceneName`, `sceneRemove`, `sceneOpacity`, `sceneOpacityOut`; pane `scene`.

- [ ] **Step 1: Write the failing test**

Append to `TestInterfaceAssets`:

```python
    def test_scene_overlay_controls_and_layer(self):
        _, _, index = self.get("/")
        html = index.decode()
        for ident in ('id="sceneRow"', 'id="sceneName"', 'id="sceneRemove"', 'id="sceneOpacity"', 'id="sceneOpacityOut"'):
            self.assertIn(ident, html)
        self.assertIn("Scene on map", html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn('map.createPane("scene")', text)
        self.assertIn("453", text[text.index('map.getPane("scene")'):text.index('map.getPane("scene")') + 80])
        self.assertIn('"/tiles/emit/" + ', text)
        self.assertIn("maxNativeZoom: 13", text[text.index("function showScene"):text.index("function clearScene")])
        show = text[text.index("function showScene"):text.index("function clearScene")]
        self.assertIn("clearScene()", show)                      # one scene at a time
        self.assertIn("map.fitBounds", show)
        self.assertIn("g.orientable", text)
        self.assertIn('class="show-scene"', text)
        self.assertIn("wireSceneLinks(popup, report)", text)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestInterfaceAssets.test_scene_overlay_controls_and_layer -v`
Expected: FAIL on `id="sceneRow"`.

- [ ] **Step 3: Edit `viz/web/index.html`**

Inside `#emitControls`, directly before `<div id="emitLegend"></div>`, insert:

```html
      <div id="sceneRow" hidden>
        <div class="inline">Scene on map: <span id="sceneName"></span>
          <button id="sceneRemove" type="button" title="Remove the scene overlay">&times;</button>
        </div>
        <label>Scene opacity <output id="sceneOpacityOut">100%</output>
          <input type="range" id="sceneOpacity" min="0" max="100" step="5" value="100">
        </label>
      </div>
```

- [ ] **Step 4: Edit `viz/web/style.css`**

Append:

```css
/* EMIT scene overlay */
#sceneRow { display: grid; gap: 5px; }
#sceneRow[hidden] { display: none; }
#sceneName { font-variant-numeric: tabular-nums; }
#sceneRemove { border: 1px solid var(--line); border-radius: 3px; background: #fff; cursor: pointer;
               font: inherit; line-height: 1; padding: 0 5px; }
#sceneRemove:hover { border-color: var(--accent); }
.readout-popup .emit-list a.show-scene { font-weight: 550; }
```

- [ ] **Step 5: Edit `viz/web/app.js`**

(a) In `state`, after `hlsSensor: "ALL"` add:

```js
    ,
    scene: null,            // {id, start} of the scene overlay on the map
    sceneOpacity: 1.0
```

(b) After the ECOSTRESS pane block (after `var ecoLoaded = false;`), add:

```js
  map.createPane("scene");
  map.getPane("scene").style.zIndex = 453;   // above ECOSTRESS boxes, below EMIT outlines
  var sceneLayer = null;
```

(c) After `drawEmitLegend`, add:

```js
  // One EMIT browse image on the map at a time, served as warped tiles.
  function showScene(id, start, bbox) {
    clearScene();
    state.scene = { id: id, start: start };
    sceneLayer = L.tileLayer("/tiles/emit/" + encodeURIComponent(id) + "/{z}/{x}/{y}.png" +
                             "?t=" + state.catalog.server_token, {
      pane: "scene", maxNativeZoom: 13, maxZoom: 15, noWrap: true,
      opacity: state.sceneOpacity, attribution: "NASA EMIT L2A browse"
    }).addTo(map);
    if (bbox && bbox.length === 4) {
      var bounds = L.latLngBounds([bbox[1], bbox[0]], [bbox[3], bbox[2]]);
      if (!map.getBounds().contains(bounds)) { map.fitBounds(bounds, { padding: [20, 20] }); }
    }
    syncSceneControl();
  }

  function clearScene() {
    if (sceneLayer) { map.removeLayer(sceneLayer); sceneLayer = null; }
    state.scene = null;
    syncSceneControl();
  }

  function syncSceneControl() {
    var row = el("sceneRow");
    row.hidden = !state.scene;
    if (state.scene) {
      el("sceneName").textContent = (state.scene.start || "").slice(0, 10) + " · " + state.scene.id;
    }
  }

  // "show" links inside an open popup: the report's entries carry the bbox to zoom to.
  function wireSceneLinks(popup, report) {
    var node = popup.getElement();
    if (!node) { return; }
    var byId = {};
    (report.emit || []).forEach(function (g) { byId[g.id] = g; });
    Array.prototype.forEach.call(node.querySelectorAll("a.show-scene"), function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        var g = byId[link.dataset.id];
        if (g) { showScene(g.id, g.start, g.bbox); }
      });
    });
  }
```

(d) In `showReadout`'s EMIT row builder, directly before `(g.browse ? ' <a href="' + g.browse + ...` add:

```js
               (g.orientable ? ' <a href="#" class="show-scene" data-id="' + g.id + '">show</a>' : "") +
```

(e) In the click handler, change `popup.setContent(showReadout(report)); wireFolds(popup);` to `popup.setContent(showReadout(report)); wireFolds(popup); wireSceneLinks(popup, report);`.

(f) In `wire()`, after the `emit` checkbox listener, add:

```js
    el("sceneRemove").addEventListener("click", clearScene);
    el("sceneOpacity").addEventListener("input", function (e) {
      state.sceneOpacity = Number(e.target.value) / 100;
      el("sceneOpacityOut").textContent = e.target.value + "%";
      if (sceneLayer) { sceneLayer.setOpacity(state.sceneOpacity); }
    });
```

(g) In the boot block, add `syncSceneControl();` after `syncHls();`.

- [ ] **Step 6: Syntax check and run the suite**

Run: `node --check viz/web/app.js && ./run.sh test 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add viz/web/index.html viz/web/style.css viz/web/app.js tests/test_server.py
git commit -m "Show an EMIT scene's browse image on the map from the readout"
```

---

## Self-Review

**Spec coverage.** §2 orientation rule and neighbour lookup → Task 1. §3.1 → Task 1. §3.2 download with `.part`, host check, GCP VRT, bilinear order-1 warp with alpha, band selection, tile cache key `emit-<id>`, transparent shortcut → Task 2. §3.3 route, 404s, 502, download lock, cache headers → Tasks 2 and 3. §3.4 "show" only for orientable rows, pane 453, one scene at a time, zoom to footprint when out of view, "Scene on map" row with opacity and remove, hidden when none → Task 4 (the `orientable`/`bbox` flags come from Task 3). §4 tests: orbit parsing, neighbour, two verified orders, `None` cases (Task 1); host check, synthetic-PNG warp with corner and transparency, single band (Task 2); route 200/404/502, cached second request, path characters (Task 3); static asserts (Task 4).

**Placeholder scan.** None.

**Type consistency.** `scene_corners(index, scene_id)` returns a list of four `[lon, lat]` and Task 2's `render_tile(scene_id, corners, z, x, y)` consumes it as such; `ensure_browse(scene_id, url, fetch=)` is called with `(scene_id, props.get("browse"))` in Task 3 and patched with the same signature in its tests; `scene_bbox` returns a tuple that Task 3 lists into `bbox` and Task 4 reads as `[minlon, minlat, maxlon, maxlat]`.

**Review Focus.** Items 1, 2 → Task 3 tests (`test_path_characters_in_the_id_never_reach_the_handler`, `test_failed_download_is_502` covers the 502 path; the null-`browse` case is covered by `test_refuses_other_hosts_and_missing_urls_before_fetching` in Task 2 raising `BrowseError`, which Task 3 maps to 502). Item 3 → Task 2 `test_stale_part_file_is_not_trusted`. Item 4 → Task 2 `test_far_tile_is_the_shared_transparent_tile_and_is_cached`. Item 5 → Task 4 static assert that `showScene` calls `clearScene()` first.
