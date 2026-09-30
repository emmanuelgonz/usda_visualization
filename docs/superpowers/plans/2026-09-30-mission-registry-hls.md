# Mission Registry Step 2: HLS onto the Tiled Archetype — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the HLS layer, counts, readout section, and EMIT pairing from the region catalog through the registry-driven routes and the first generated sidebar block, then retire `viz/hls.py`, `viz/fetch_hls.py`, and the hand-written HLS interface, with no visible change.

**Architecture:** The server loads the registry at start and reads the catalog through a per-thread read-only handle cached by file time (`catalog.catalog_for`), degrading to 503 only on a lock. Tiled serving lives in `viz/archetypes/tiled.py` (counts, acquisitions, tile GeoJSON, clear rules) with filter parsing in `viz/filters.py`. Routes `/api/missions/<key>/tiles.geojson` and `/counts` replace `/api/hls/*`; `/api/catalog` gains `missions`; the point report carries the HLS block under `missions.hls`. `viz/hls_pairs.py` reads the catalog with the same centroid rule. The browser gets `missions.js`, which generates the HLS block from the catalog's `missions` list and owns the generic tiled layer; EMIT and ECOSTRESS stay hand-written until steps 3 and 4.

**Tech Stack:** Python 3.10 standard library + GDAL, vanilla JavaScript on Leaflet 1.9.4, unittest, node for runtime asserts.

**Spec:** `docs/superpowers/specs/2026-09-30-mission-registry-design.md` (§4 serving duties, §5 routes, §7 interface for the tiled part, §9 step 2, §10 testing). Step 1 is merged (PR #12).

## Global Constraints

- Every Python invocation is prefixed with `PYTHONNOUSERSITE=1`; the suite is `PYTHONNOUSERSITE=1 ./run.sh test` and must stay green with pristine output at every task.
- Standard library plus GDAL only; vanilla JavaScript, no build step. Style functions return the same key set on every call.
- No visible change: the HLS checkbox, its mode, cloud, and sensor controls, the tile colouring, the tooltips, the legend, the readout section, the EMIT "Mark ECOSTRESS + HLS coincidence" behaviour, and the per-scene HLS tags look and behave as now.
- Registry: `viz/missions.json`; the server refuses to start on a `registry.RegistryError`, printing the message.
- Catalog: `paths.catalog_db(region)`; read-only per-thread handles with the 0.5 s timeout; only a lock error ("locked" or "busy" in the message) counts as busy (503 on catalog routes, null mission blocks in the readout); any other error is a 500.
- Filter parameters on the new routes are `f.<attribute>=<value>` validated against the registry (`max` takes an integer 0 to 100 for cloud, `choice` one of its values; unknown names 400). The point route keeps its existing `start`, `end`, `cloud`, `sensor`, `window` parameters until step 4.
- Commits are made by the controller after review; implementers leave work uncommitted. Never write attribution anywhere.

## Review Focus

1. A catalog whose `months` table has HLS rows but whose `tiles` table has no `mgrs` rows (a refresh interrupted before `finish_grids`): the layer stays off with the "tile rings not computed" reason and nothing crashes (Task 2 test `test_catalog_without_tiles_disables_the_layer`).
2. A `f.cloud` value such as `abc`, `-5`, or `150`, and a `f.sensor` of `X30`: 400 with a message naming the parameter (Task 1 test `test_bad_filter_values`; Task 2 test `test_counts_reject_bad_parameters`).
3. A point inside two overlapping MGRS tiles: both tiles listed in the readout block, acquisitions attributed to the right tile, and the EMIT tag chosen over both (Task 2 test `test_point_in_overlapping_tiles_lists_both`).
4. The catalog file replaced while the server runs (a refresh finished): the next request reopens it and serves the new counts without a restart (Task 1 test `test_catalog_for_reopens_on_change`).
5. `hls_pairs` against a catalog with no `mgrs` tiles: the scene gets an empty list and the command reports the missing rings rather than crashing (Task 3 test `test_pairs_without_tiles_are_empty`).

---

### Task 1: Tiled serving, filters, and the per-thread catalog handle

**Files:**
- Create: `viz/filters.py`
- Modify: `viz/archetypes/tiled.py` (serve side), `viz/catalog.py` (`catalog_for`, `is_busy`, `tile_rings` already exists)
- Test: `tests/test_filters.py`, `tests/test_archetype_tiled.py` (new class `TestServe`), `tests/test_catalog.py` (new class `TestCatalogFor`)

**Interfaces:**
- Consumes: `registry.Mission` (filters with `attribute`, `control`, `values`, `default`), `catalog.Catalog`, `spatial.RingIndex`.
- Produces:
  - `filters.parse(mission, query, prefix="f.") -> dict` mapping attribute name to a validated value; missing filters take their defaults; `filters.FilterError(ValueError)` with the parameter name for a bad value or an unknown `f.` name. `query` is the `parse_qs` dict (values are lists).
  - `filters.passes(row, mission, values) -> bool`: `max` controls require `row[attr]` not None and `<= value`; `choice` controls pass when the value is `ALL` or equals `row[attr]`.
  - `filters.describe(mission) -> list[dict]` JSON-able filter descriptions `{attribute, control, default, label, values}` for the catalog route.
  - `tiled.counts(cat, mission, start, end, values) -> dict[tile, n]` over `start <= date <= end` (inclusive date strings compared against `substr(start, 1, 10)`), applying `passes`; tiles with zero omitted.
  - `tiled.acquisitions(cat, mission, tiles, start, end) -> list[dict]` with keys `tile`, `date` (`start[:10]`), `time` (`start`), and every registry attribute (`sensor`, `cloud`), oldest first then by tile.
  - `tiled.tiles_geojson(cat, grid) -> dict` FeatureCollection with a `tile` property per ring, tiles sorted.
  - `tiled.nearest_clear(rows, date, window) -> dict | None` as `hls.nearest_clear` today (`{date, sensor, cloud, dt}`, ties to the earlier time), rows already filtered.
  - `catalog.is_busy(exc) -> bool` true for an `OperationalError` whose message contains "locked" or "busy".
  - `catalog.catalog_for(path) -> (Catalog, dict[grid, RingIndex]) | None` per thread, read-only, reopened when the file's mtime changes, `None` when absent; the `RingIndex` payload per tile is the tile id; raises the lock error (caller decides) when the index build hits the lock, closing the connection first.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_filters.py`:

```python
import unittest

from viz import filters, registry

REG = registry.parse({
    "region": {"name": "T", "bbox": [-125.0, 24.4, -66.9, 49.4]},
    "missions": [{
        "key": "hls", "name": "HLS", "label": "HLS coverage", "archetype": "tiled", "grid": "mgrs",
        "cmr": [{"short_name": "HLSL30", "version": "2.0", "implies": {"sensor": "L30"}}],
        "since": "2022-01",
        "tile_from": {"field": "title", "pattern": "^HLS\\.[LS]30\\.(T[0-9]{2}[A-Z]{3})\\."},
        "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}, "sensor": {"from": "sensor", "type": "text"}},
        "filters": [{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud"},
                    {"attribute": "sensor", "control": "choice", "values": ["ALL", "L30", "S30"], "default": "ALL", "label": "Sensor"}],
        "browse": {"source": "links", "match": "\\.jpg$"}, "links": {}, "style": {}}]})
HLS = REG.mission("hls")


class TestParse(unittest.TestCase):
    def test_defaults_and_given_values(self):
        self.assertEqual(filters.parse(HLS, {}), {"cloud": 30, "sensor": "ALL"})
        self.assertEqual(filters.parse(HLS, {"f.cloud": ["55"], "f.sensor": ["S30"]}), {"cloud": 55, "sensor": "S30"})
        self.assertEqual(filters.parse(HLS, {"cloud": ["10"], "sensor": ["L30"]}, prefix=""), {"cloud": 10, "sensor": "L30"})

    def test_bad_filter_values(self):
        for query, word in (({"f.cloud": ["abc"]}, "cloud"), ({"f.cloud": ["-5"]}, "cloud"), ({"f.cloud": ["150"]}, "cloud"),
                            ({"f.sensor": ["X30"]}, "sensor"), ({"f.nope": ["1"]}, "nope")):
            with self.assertRaisesRegex(filters.FilterError, word):
                filters.parse(HLS, query)

    def test_passes(self):
        values = {"cloud": 30, "sensor": "ALL"}
        self.assertTrue(filters.passes({"cloud": 30, "sensor": "S30"}, HLS, values))
        self.assertFalse(filters.passes({"cloud": 31, "sensor": "S30"}, HLS, values))
        self.assertFalse(filters.passes({"cloud": None, "sensor": "S30"}, HLS, values))
        self.assertFalse(filters.passes({"cloud": 5, "sensor": "S30"}, HLS, {"cloud": 30, "sensor": "L30"}))
        self.assertTrue(filters.passes({"cloud": 5, "sensor": "L30"}, HLS, {"cloud": 30, "sensor": "L30"}))

    def test_describe_is_json_able(self):
        described = filters.describe(HLS)
        self.assertEqual(described[0], {"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud", "values": []})
        self.assertEqual(described[1]["values"], ["ALL", "L30", "S30"])
```

Append to `tests/test_archetype_tiled.py` (the existing `MISSION` fixture already declares `cloud`, `sensor`, and `daynight`; add filters to it so `passes` has something to read: change its `"filters": []` to the two filters used in `tests/test_filters.py`):

```python
import shutil
import tempfile
from pathlib import Path

from viz import catalog


def tiled_row(gid, start, tile, cloud, sensor):
    return {"id": gid, "start": start, "end": start, "tile": tile, "cloud": cloud, "sensor": sensor}


class TestServe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cat = catalog.Catalog(self.tmp / "c.sqlite")
        self.addCleanup(self.cat.close)
        self.cat.replace_month("hls", "2025-07", [
            tiled_row("a", "2025-07-18T17:00:00Z", "T99ZZZ", 10, "S30"),
            tiled_row("b", "2025-07-23T17:00:00Z", "T99ZZZ", 10, "S30"),
            tiled_row("c", "2025-07-20T17:00:00Z", "T98ZZZ", 0, "S30"),
            tiled_row("d", "2025-07-21T16:00:00Z", "T99ZZZ", 80, "L30"),
            tiled_row("e", "2025-07-22T16:00:00Z", "T99ZZZ", None, "L30"),
        ], "x")
        self.cat.put_tiles("mgrs", [("T99ZZZ", [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]),
                                    ("T98ZZZ", [[20, 0], [21, 0], [21, 1], [20, 1], [20, 0]])])

    def test_counts_apply_range_and_filters(self):
        self.assertEqual(tiled.counts(self.cat, MISSION, "2025-07-15", "2025-07-31", {"cloud": 30, "sensor": "ALL"}),
                         {"T99ZZZ": 2, "T98ZZZ": 1})
        self.assertEqual(tiled.counts(self.cat, MISSION, "2025-07-15", "2025-07-31", {"cloud": 30, "sensor": "L30"}), {})
        self.assertEqual(tiled.counts(self.cat, MISSION, "2025-07-19", "2025-07-22", {"cloud": 100, "sensor": "ALL"}),
                         {"T99ZZZ": 1, "T98ZZZ": 1})     # the NULL cloud row never counts

    def test_acquisitions_are_oldest_first_with_attributes(self):
        rows = tiled.acquisitions(self.cat, MISSION, ["T99ZZZ"], "2025-07-15", "2025-07-31")
        self.assertEqual([(r["date"], r["sensor"], r["cloud"]) for r in rows],
                         [("2025-07-18", "S30", 10.0), ("2025-07-21", "L30", 80.0), ("2025-07-22", "L30", None), ("2025-07-23", "S30", 10.0)])
        self.assertEqual(rows[0]["time"], "2025-07-18T17:00:00Z")
        self.assertEqual(rows[0]["tile"], "T99ZZZ")
        self.assertEqual(tiled.acquisitions(self.cat, MISSION, [], "2025-07-15", "2025-07-31"), [])

    def test_tiles_geojson(self):
        geo = tiled.tiles_geojson(self.cat, "mgrs")
        self.assertEqual([f["properties"]["tile"] for f in geo["features"]], ["T98ZZZ", "T99ZZZ"])
        self.assertEqual(geo["features"][1]["geometry"]["coordinates"][0][0], [0, 0])

    def test_nearest_clear_ties_to_the_earlier_time(self):
        rows = [{"date": "2025-07-18", "time": "2025-07-18T17:00:00Z", "sensor": "S30", "cloud": 10.0},
                {"date": "2025-07-22", "time": "2025-07-22T16:00:00Z", "sensor": "L30", "cloud": 5.0}]
        self.assertEqual(tiled.nearest_clear(rows, "2025-07-20", 7), {"date": "2025-07-18", "sensor": "S30", "cloud": 10.0, "dt": -2})
        self.assertIsNone(tiled.nearest_clear(rows, "2025-08-20", 7))
        self.assertIsNone(tiled.nearest_clear([], "2025-07-20", 7))
```

Append to `tests/test_catalog.py`:

```python
import os
import time

from viz import catalog as catalog_module


class TestCatalogFor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "c.sqlite"
        writer = catalog.Catalog(self.path)
        writer.put_tiles("mgrs", [("T99ZZZ", RING)])
        writer.close()

    def tearDown(self):
        catalog_module._drop_cached()

    def test_returns_handle_and_tile_index_and_none_when_absent(self):
        cat, indexes = catalog.catalog_for(self.path)
        self.assertEqual(indexes["mgrs"].covering(-99.5, 40.5), ["T99ZZZ"])
        self.assertIs(catalog.catalog_for(self.path)[0], cat)          # cached for this thread
        self.assertIsNone(catalog.catalog_for(self.tmp / "none.sqlite"))

    def test_catalog_for_reopens_on_change(self):
        first, _ = catalog.catalog_for(self.path)
        writer = catalog.Catalog(self.path)
        writer.put_tiles("mgrs", [("T98ZZZ", [[20, 40], [21, 40], [21, 41], [20, 41], [20, 40]])])
        writer.close()
        stamp = os.stat(self.path).st_mtime_ns + 1_000_000
        os.utime(self.path, ns=(stamp, stamp))
        second, indexes = catalog.catalog_for(self.path)
        self.assertIsNot(second, first)
        self.assertEqual(indexes["mgrs"].covering(20.5, 40.5), ["T98ZZZ"])

    def test_is_busy(self):
        self.assertTrue(catalog.is_busy(sqlite3.OperationalError("database is locked")))
        self.assertTrue(catalog.is_busy(sqlite3.OperationalError("database table is busy")))
        self.assertFalse(catalog.is_busy(sqlite3.OperationalError("no such column: nope")))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_filters tests.test_archetype_tiled.TestServe tests.test_catalog.TestCatalogFor -v`
Expected: FAIL with `ImportError` for `filters`, `AttributeError` for `tiled.counts` and `catalog.catalog_for`.

- [ ] **Step 3: Write `viz/filters.py`**

```python
"""Filter values for a mission's controls, parsed from a query string and applied to rows.

A registry `max` control takes an integer 0 to 100 (the cloud threshold); a
`choice` control takes one of its values, with ALL meaning no filter.
"""


class FilterError(ValueError):
    """A bad or unknown filter parameter; the message names it."""


def parse(mission, query, prefix="f."):
    """{attribute: value} for the mission's filters from a parse_qs dict; defaults when absent."""
    known = {f.attribute: f for f in mission.filters}
    values = {name: f.default for name, f in known.items()}
    for key, raw in query.items():
        if not key.startswith(prefix):
            continue
        name = key[len(prefix):]
        if name not in known:
            raise FilterError(f"unknown filter {name!r}")
        control = known[name]
        text = raw[0] if isinstance(raw, list) else raw
        if control.control == "max":
            try:
                number = int(text)
            except ValueError:
                raise FilterError(f"{name} must be an integer") from None
            if not 0 <= number <= 100:
                raise FilterError(f"{name} must be 0-100")
            values[name] = number
        else:
            if text not in control.values:
                raise FilterError(f"{name} must be one of {', '.join(control.values)}")
            values[name] = text
    return values


def passes(row, mission, values):
    """True when the row satisfies every filter value."""
    for control in mission.filters:
        value = values.get(control.attribute, control.default)
        have = row.get(control.attribute)
        if control.control == "max":
            if have is None or have > value:
                return False
        elif value != "ALL" and have != value:
            return False
    return True


def describe(mission):
    """JSON-able descriptions of the mission's filters for the catalog route."""
    return [{"attribute": f.attribute, "control": f.control, "default": f.default, "label": f.label,
             "values": list(f.values)} for f in mission.filters]
```

- [ ] **Step 4: Add the serve side to `viz/archetypes/tiled.py`**

Append:

```python
import datetime
import json

from viz import filters


def counts(cat, mission, start, end, values):
    """Clear acquisitions per tile over the inclusive date range, filters applied; zero tiles omitted."""
    sql = ("SELECT tile, cloud, sensor, daynight, attrs FROM granules "
           "WHERE mission = ? AND tile IS NOT NULL AND substr(start, 1, 10) BETWEEN ? AND ?")
    out = {}
    for row in cat.conn.execute(sql, (mission.key, start, end)):
        if filters.passes(row, mission, values):
            out[row["tile"]] = out.get(row["tile"], 0) + 1
    return out


def acquisitions(cat, mission, tiles, start, end):
    """Every acquisition of the given tiles in the inclusive date range, oldest first then by tile."""
    tiles = list(tiles)
    if not tiles:
        return []
    marks = ",".join("?" * len(tiles))
    sql = (f"SELECT tile, start, cloud, sensor, daynight, orbit FROM granules WHERE mission = ? "
           f"AND tile IN ({marks}) AND substr(start, 1, 10) BETWEEN ? AND ? ORDER BY start, tile")
    rows = []
    for row in cat.conn.execute(sql, [mission.key, *tiles, start, end]):
        item = {"tile": row["tile"], "date": row["start"][:10], "time": row["start"]}
        for name in mission.attributes:
            item[name] = row[name] if name in row.keys() else None
        rows.append(item)
    return rows


def tiles_geojson(cat, grid):
    features = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                 "properties": {"tile": tile}} for tile, ring in cat.tile_rings(grid)]
    return {"type": "FeatureCollection", "features": features}


def nearest_clear(rows, date, window):
    """The row nearest the date within ±window days, ties to the earlier time; None if none.

    rows must already be filtered. dt is the acquisition date minus the given
    date in whole calendar days.
    """
    base = datetime.date.fromisoformat(date)
    best = None
    for row in rows:
        dt = (datetime.date.fromisoformat(row["date"]) - base).days
        if abs(dt) > window:
            continue
        key = (abs(dt), row["time"])
        if best is None or key < best[0]:
            best = (key, {"date": row["date"], "sensor": row.get("sensor"), "cloud": row.get("cloud"), "dt": dt})
    return best[1] if best else None
```

`counts` reads the attribute columns the row factory exposes; `filters.passes` indexes the `sqlite3.Row` by name, which works because `Row` supports `row.get`? It does not: add at the top of `counts` a conversion `row = dict(row)` before `passes` (write it as `if filters.passes(dict(row), mission, values)`).

- [ ] **Step 5: Add `is_busy` and `catalog_for` to `viz/catalog.py`**

Append:

```python
import os
import threading

from viz import spatial


def is_busy(exc):
    """True for the lock errors a refresh causes; any other OperationalError is a fault."""
    text = str(exc)
    return "locked" in text or "busy" in text


_local = threading.local()


def _drop_cached():
    cached = getattr(_local, "entry", None)
    _local.entry = None
    if cached is not None:
        cached[1][0].close()


def catalog_for(path):
    """(Catalog, {grid: RingIndex of tile ids}) for this thread, read-only, reopened when the file changes.

    None when the file is absent. Raises the lock error when building the tile
    indexes hits a refresh's write lock, after closing the connection.
    """
    path = Path(path)
    if not path.is_file():
        _drop_cached()
        return None
    key = (str(path), os.stat(path).st_mtime_ns)
    cached = getattr(_local, "entry", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    _drop_cached()
    cat = Catalog(path, read_only=True)
    try:
        grids = [r["grid"] for r in cat.conn.execute("SELECT DISTINCT grid FROM tiles ORDER BY grid")]
        indexes = {grid: spatial.RingIndex((ring, tile) for tile, ring in cat.tile_rings(grid)) for grid in grids}
    except Exception:
        cat.close()
        raise
    _local.entry = (key, (cat, indexes))
    return cat, indexes
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_filters tests.test_archetype_tiled tests.test_catalog -v`
Expected: PASS. Then the full suite once.

- [ ] **Step 7: Commit point**

Message: `Add tiled serving, filter parsing, and the per-thread catalog handle`.

---

### Task 2: Server routes on the catalog and the registry

**Files:**
- Modify: `viz/tileserver.py` (registry at start; `catalog_read`; `/api/catalog` `missions`; `/api/missions/<key>/tiles.geojson` and `/counts`; the point report's HLS block from the catalog under `missions`; remove `/api/hls/*`, `hls_read`, `HLS_SENSORS`, `HLS_BUSY`, the `hls` import)
- Modify: `tests/test_server.py` (fixture builds a catalog; `TestHlsRoutes` rewritten as `TestMissionRoutes`; the `_saved` tuples gain `paths.CATALOG_DATA`)

**Interfaces:**
- Consumes: Task 1; `registry.load`, `paths.catalog_db`, `filters`, `tiled.*`, `catalog.catalog_for`, `catalog.is_busy`.
- Produces:
  - Module-level `REGISTRY = registry.load()` guarded: `main()` catches `registry.RegistryError`, prints `registry: <message>` to stderr, returns 2; `CATALOG_PATH()` returns `paths.catalog_db(REGISTRY.region.name)` (a function, so tests that repoint `paths.CATALOG_DATA` take effect).
  - `catalog_read(work=None) -> (value, busy)` mirroring today's `hls_read` over `catalog.catalog_for(CATALOG_PATH())`.
  - `/api/catalog` gains `missions`: a list, one per registry mission in file order, `{key, name, label, archetype, grid, filters: filters.describe(m), style, count, fetched, tiles}` where `count` and `fetched` come from `cat.summary(key)` (0 and null without a catalog), `tiles` is the grid's tile count for tiled missions; plus `catalog_busy` (bool). The old `hls_count`, `hls_tiles`, `hls_fetched`, `hls_busy` fields are removed. `emit_hls_paired` stays (still computed from the EMIT file).
  - `GET /api/missions/<key>/tiles.geojson`: 404 `unknown mission` for a key not in the registry, 404 `mission <key> is not tiled` for a swath key, 503 when busy, 404 `no catalog; run ./run.sh migrate once, then ./run.sh refresh` when absent, else the grid's FeatureCollection (`application/geo+json`), serialised once per handle and cached on the `Catalog` object as `_tiles_bytes[grid]`.
  - `GET /api/missions/<key>/counts?start&end&f.<attr>`: same 404s and 503; 400 on missing or bad dates (the existing date rules: both given, `YYYY-MM-DD`, calendar dates, start not after end) or a `FilterError`; `{"counts": {tile: n}}`.
  - `/api/point`: the HLS block moves to `report["missions"] = {"hls": block}` (same block shape as today's `report["hls"]`: `start, end, cloud, sensor, window, tiles[{tile, clear, acq[{date, time, sensor, cloud}]}]`), built from the catalog (`indexes["mgrs"].covering`, `tiled.acquisitions`, `filters.passes` with `{"cloud": cloud, "sensor": sensor}`), and each EMIT granule's `hls` tag from `tiled.nearest_clear`; `report["missions"]` is `None` when the catalog is busy or absent (and no `hls` tags), matching today's null `hls`. The old top-level `report["hls"]` key is removed.

- [ ] **Step 1: Rewrite the server test fixture and the HLS route tests**

In `tests/test_server.py`:

1. In `_saved` and the restore tuple add `paths.CATALOG_DATA`; in `setUpClass` set `paths.CATALOG_DATA = cls.tmp / "catalog"`; drop `paths.HLS_DB` from both tuples and the `cls.tmp` assignment.
2. Replace the HLS store block (from `# An HLS store with one tile...` through `hls_store.close()`) with a catalog built the same way:

```python
        # A catalog with one HLS tile over the fixture centre and one far away.
        from viz import catalog as catalog_module, registry

        def acq(gid, start, tile, cloud, sensor):
            return {"id": gid, "start": start, "end": start, "tile": tile, "cloud": cloud, "sensor": sensor}

        cat = catalog_module.Catalog(paths.catalog_db("CONUS"))
        cat.replace_month("hls", "2025-07", [
            acq("HLS.S30.T99ZZZ.2025199T170000.v2.0", "2025-07-18T17:00:00Z", "T99ZZZ", 10, "S30"),
            acq("HLS.S30.T99ZZZ.2025204T170000.v2.0", "2025-07-23T17:00:00Z", "T99ZZZ", 10, "S30"),
            acq("HLS.S30.T98ZZZ.2025201T170000.v2.0", "2025-07-20T17:00:00Z", "T98ZZZ", 0, "S30"),
            acq("HLS.L30.T99ZZZ.2025202T160000.v2.0", "2025-07-21T16:00:00Z", "T99ZZZ", 80, "L30"),
        ], "2025-08-02T00:00:00+00:00")
        cat.put_tiles("mgrs", [("T99ZZZ", square(cls.lon, cls.lat, 0.5)), ("T98ZZZ", square(cls.lon + 20, cls.lat, 0.5))])
        # Attach the HLS lists to the EMIT fixture the way ./run.sh hls would.
        from viz import hls_pairs
        emit_doc = json.loads(paths.EMIT_FOOTPRINTS.read_text())
        hls_pairs.pair(emit_doc["features"], cat, registry.load().mission("hls"),
                       __import__("viz.spatial", fromlist=["RingIndex"]).RingIndex((ring, tile) for tile, ring in cat.tile_rings("mgrs")))
        paths.EMIT_FOOTPRINTS.write_text(json.dumps(emit_doc))
        cat.close()
```

   (Task 3 changes `hls_pairs.pair`'s signature to `(features, cat, mission, index, days, cap)`; this fixture is written against that signature, so Task 2 must temporarily keep the old `hls_pairs` working. Rule for the implementer: in Task 2 write the fixture with a local helper that reproduces the pairing directly from the catalog rows (centroid in tile T99ZZZ, acquisitions within 15 days, nearest first, one entry per date and sensor with the lowest cloud), so the fixture does not depend on `hls_pairs` at all; Task 3 then keeps the helper. Concretely:)

```python
        # Attach the HLS lists to the EMIT fixture the way ./run.sh hls does: centroid tile, ±15 days.
        import datetime as _dt
        from viz import spatial
        from viz.archetypes import tiled
        hls_mission = registry.load().mission("hls")
        index = spatial.RingIndex((ring, tile) for tile, ring in cat.tile_rings("mgrs"))
        emit_doc = json.loads(paths.EMIT_FOOTPRINTS.read_text())
        for feature in emit_doc["features"]:
            ring = feature["geometry"]["coordinates"][0]
            pts = ring[:-1] if ring[0] == ring[-1] else ring
            lon, lat = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            tiles = index.covering(lon, lat)
            hits = []
            start = feature["properties"].get("start")
            if tiles and start:
                base = _dt.date.fromisoformat(start[:10])
                lo, hi = (base - _dt.timedelta(days=15)).isoformat(), (base + _dt.timedelta(days=15)).isoformat()
                best = {}
                for row in tiled.acquisitions(cat, hls_mission, tiles, lo, hi):
                    key = (row["date"], row["sensor"])
                    if key in best and (row["cloud"] is None or (best[key]["cloud"] is not None and best[key]["cloud"] <= row["cloud"])):
                        continue
                    best[key] = {"date": row["date"], "sensor": row["sensor"], "cloud": row["cloud"],
                                 "dt": (_dt.date.fromisoformat(row["date"]) - base).days, "_t": row["time"]}
                hits = sorted(best.values(), key=lambda h: (abs(h["dt"]), h["_t"]))
                for h in hits:
                    del h["_t"]
            feature["properties"]["hls_near"] = hits[:12]
        paths.EMIT_FOOTPRINTS.write_text(json.dumps(emit_doc))
        cat.close()
```

3. Replace class `TestHlsRoutes` with `TestMissionRoutes`, keeping every existing test's intent and changing only the routes, keys, and parameter names:

```python
class TestMissionRoutes(ServerTestCase):
    HLS_QUERY = "&start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL&window=7"

    def test_catalog_lists_missions_with_counts(self):
        catalog = json.loads(self.get("/api/catalog")[2])
        by_key = {m["key"]: m for m in catalog["missions"]}
        self.assertEqual(list(by_key), ["emit", "eco", "hls"])
        hls = by_key["hls"]
        self.assertEqual((hls["archetype"], hls["grid"], hls["count"], hls["tiles"], hls["fetched"]),
                         ("tiled", "mgrs", 4, 2, "2025-08-02T00:00:00+00:00"))
        self.assertEqual(hls["filters"][0]["attribute"], "cloud")
        self.assertEqual(by_key["emit"]["count"], 0)          # not migrated in this fixture
        self.assertFalse(catalog["catalog_busy"])
        for old in ("hls_count", "hls_tiles", "hls_fetched", "hls_busy"):
            self.assertNotIn(old, catalog)

    def test_tiles_are_served_as_geojson(self):
        status, ctype, body = self.get("/api/missions/hls/tiles.geojson")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/geo+json")
        self.assertEqual([f["properties"]["tile"] for f in json.loads(body)["features"]], ["T98ZZZ", "T99ZZZ"])
        for route, word in (("/api/missions/nope/tiles.geojson", "unknown mission"),
                            ("/api/missions/emit/tiles.geojson", "not tiled")):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(route)
            self.assertEqual(ctx.exception.code, 404)
            self.assertIn(word, ctx.exception.read().decode())

    def test_counts_apply_range_and_filters(self):
        _, ctype, body = self.get("/api/missions/hls/counts?start=2025-07-15&end=2025-07-31&f.cloud=30&f.sensor=ALL")
        self.assertEqual(ctype, "application/json")
        self.assertEqual(json.loads(body), {"counts": {"T99ZZZ": 2, "T98ZZZ": 1}})
        _, _, body = self.get("/api/missions/hls/counts?start=2025-07-15&end=2025-07-31&f.cloud=30&f.sensor=L30")
        self.assertEqual(json.loads(body), {"counts": {}})
        _, _, body = self.get("/api/missions/hls/counts?start=2025-07-19&end=2025-07-22&f.cloud=100")
        self.assertEqual(json.loads(body), {"counts": {"T99ZZZ": 1, "T98ZZZ": 1}})

    def test_counts_reject_bad_parameters(self):
        for query in ("start=2025-07-15&end=2025-07-31&f.sensor=X30",
                      "start=2025-7-15&end=2025-07-31",
                      "start=2025-07-15&end=2025-07-31&f.cloud=abc",
                      "start=2025-07-15&end=2025-07-31&f.cloud=150",
                      "end=2025-07-31",
                      "start=2025-13-40&end=2025-07-31",
                      "start=2025-08-01&end=2025-07-01",
                      "start=2025-07-15&end=2025-07-31&f.nope=1"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/missions/hls/counts?" + query)
            self.assertEqual(ctx.exception.code, 400)

    def test_point_lists_covering_tiles_with_clear_counts(self):
        report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        self.assertNotIn("hls", report)
        block = report["missions"]["hls"]
        self.assertEqual((block["start"], block["end"], block["cloud"], block["sensor"], block["window"]),
                         ("2025-07-15", "2025-07-31", 30, "ALL", 7))
        self.assertEqual(len(block["tiles"]), 1)
        tile = block["tiles"][0]
        self.assertEqual((tile["tile"], tile["clear"]), ("T99ZZZ", 2))
        self.assertEqual([a["date"] for a in tile["acq"]], ["2025-07-18", "2025-07-21", "2025-07-23"])
        self.assertEqual(tile["acq"][1], {"date": "2025-07-21", "time": "2025-07-21T16:00:00Z", "sensor": "L30", "cloud": 80.0})

    def test_point_tags_emit_scenes_with_the_nearest_clear_acquisition(self):
        report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        by_id = {g["id"]: g for g in report["emit"]}
        self.assertEqual(by_id["near-new"]["hls"], {"date": "2025-07-18", "sensor": "S30", "cloud": 10.0, "dt": -2})
        self.assertIsNone(by_id["near-old"]["hls"])
        report = json.loads(self.get(self.point_url() + "&start=2025-07-15&end=2025-07-31&cloud=30&sensor=L30&window=7")[2])
        self.assertIsNone({g["id"]: g for g in report["emit"]}["near-new"]["hls"])
        self.assertEqual(report["missions"]["hls"]["tiles"][0]["clear"], 0)

    def test_point_in_overlapping_tiles_lists_both(self):
        # A second tile overlapping the fixture centre, with one acquisition of its own.
        from viz import catalog as catalog_module
        cat = catalog_module.Catalog(paths.catalog_db("CONUS"))
        cat.put_tiles("mgrs", [("T97ZZZ", square(self.lon + 0.2, self.lat, 0.5))])
        cat.replace_month("hls", "2025-06", [
            {"id": "HLS.S30.T97ZZZ.2025180T170000.v2.0", "start": "2025-06-29T17:00:00Z", "end": "2025-06-29T17:00:00Z",
             "tile": "T97ZZZ", "cloud": 0, "sensor": "S30"}], "x")
        cat.close()
        stamp = os.stat(paths.catalog_db("CONUS")).st_mtime_ns + 1_000_000
        os.utime(paths.catalog_db("CONUS"), ns=(stamp, stamp))
        try:
            report = json.loads(self.get(self.point_url() + "&start=2025-06-15&end=2025-07-31&cloud=30&sensor=ALL&window=30")[2])
            tiles = {t["tile"]: t for t in report["missions"]["hls"]["tiles"]}
            self.assertEqual(sorted(tiles), ["T97ZZZ", "T99ZZZ"])
            self.assertEqual([a["date"] for a in tiles["T97ZZZ"]["acq"]], ["2025-06-29"])
            self.assertEqual(tiles["T99ZZZ"]["clear"], 2)
        finally:
            cat = catalog_module.Catalog(paths.catalog_db("CONUS"))
            cat.conn.execute("DELETE FROM tiles WHERE tile = 'T97ZZZ'")
            cat.conn.execute("DELETE FROM granules WHERE tile = 'T97ZZZ'")
            cat.conn.execute("DELETE FROM months WHERE mission = 'hls' AND month = '2025-06'")
            cat.conn.commit()
            cat.close()
            stamp = os.stat(paths.catalog_db("CONUS")).st_mtime_ns + 1_000_000
            os.utime(paths.catalog_db("CONUS"), ns=(stamp, stamp))

    def test_point_rejects_a_lone_start_or_end(self):
        for query in ("&start=2025-07-15", "&end=2025-07-31"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(self.point_url() + query)
            self.assertEqual(ctx.exception.code, 400)
            self.assertIn("go together", ctx.exception.read().decode())

    def test_point_rejects_bad_and_reversed_dates_like_the_counts_route(self):
        for query, message in (("&start=2025-13-40&end=2025-07-31", "calendar"),
                               ("&start=2025-08-01&end=2025-07-01", "after end")):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(self.point_url() + query)
            self.assertEqual(ctx.exception.code, 400)
            self.assertIn(message, ctx.exception.read().decode())
        report = json.loads(self.get(self.point_url() + "&start=2025-07-20&end=2025-07-20&cloud=30&sensor=ALL&window=7")[2])
        self.assertEqual((report["missions"]["hls"]["start"], report["missions"]["hls"]["end"]), ("2025-07-20", "2025-07-20"))

    def test_a_query_fault_is_a_500_not_busy(self):
        import sqlite3
        from unittest import mock
        with mock.patch.object(tileserver.catalog, "catalog_for", side_effect=sqlite3.OperationalError("no such column: nope")):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/missions/hls/counts?start=2025-07-15&end=2025-07-31")
            self.assertEqual(ctx.exception.code, 500)
            self.assertIn("no such column", ctx.exception.read().decode())

    def test_a_locked_catalog_is_503_and_a_null_missions_block(self):
        import sqlite3
        from unittest import mock
        with mock.patch.object(tileserver.catalog, "catalog_for", side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/missions/hls/counts?start=2025-07-15&end=2025-07-31")
            self.assertEqual(ctx.exception.code, 503)
            report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
            self.assertIsNone(report["missions"])
            catalog = json.loads(self.get("/api/catalog")[2])
            self.assertTrue(catalog["catalog_busy"])

    def test_point_defaults_the_range_to_the_week_window(self):
        report = json.loads(self.get(self.point_url() + "&week=30")[2])
        self.assertEqual((report["missions"]["hls"]["start"], report["missions"]["hls"]["end"]), ("2024-07-21", "2024-08-04"))
        self.assertEqual(report["missions"]["hls"]["tiles"][0]["acq"], [])

    def test_point_payload_omits_the_map_only_pairing_list(self):
        report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        for g in report["emit"]:
            self.assertNotIn("hls_near", g)

    def test_point_outside_every_tile_ring_leaves_emit_scenes_untagged(self):
        url = (f"/api/point?lon={self.lon + 10:.6f}&lat={self.lat:.6f}"
               "&crop=corn&year=2024&cdl_year=2024" + self.HLS_QUERY)
        report = json.loads(self.get(url)[2])
        self.assertEqual(report["missions"]["hls"]["tiles"], [])
        self.assertEqual([g["id"] for g in report["emit"]], ["no-tile"])
        for g in report["emit"]:
            self.assertNotIn("hls", g)

    def test_point_without_a_week_has_a_null_range_but_still_tags_scenes(self):
        report = json.loads(self.get(self.point_url())[2])
        block = report["missions"]["hls"]
        self.assertEqual((block["start"], block["end"]), (None, None))
        self.assertEqual(block["tiles"], [{"tile": "T99ZZZ", "clear": 0, "acq": []}])
        by_id = {g["id"]: g for g in report["emit"]}
        self.assertEqual(by_id["near-new"]["hls"], {"date": "2025-07-18", "sensor": "S30", "cloud": 10.0, "dt": -2})
        self.assertIsNone(by_id["near-old"]["hls"])

    def test_catalog_without_tiles_disables_the_layer(self):
        from viz import catalog as catalog_module
        path = paths.catalog_db("CONUS")
        cat = catalog_module.Catalog(path)
        saved = cat.tile_rings("mgrs")
        cat.conn.execute("DELETE FROM tiles")
        cat.conn.commit()
        cat.close()
        stamp = os.stat(path).st_mtime_ns + 1_000_000
        os.utime(path, ns=(stamp, stamp))
        try:
            catalog = json.loads(self.get("/api/catalog")[2])
            hls = {m["key"]: m for m in catalog["missions"]}["hls"]
            self.assertEqual((hls["count"], hls["tiles"]), (4, 0))
            geo = json.loads(self.get("/api/missions/hls/tiles.geojson")[2])
            self.assertEqual(geo["features"], [])
            report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
            self.assertEqual(report["missions"]["hls"]["tiles"], [])
        finally:
            cat = catalog_module.Catalog(path)
            cat.put_tiles("mgrs", saved)
            cat.close()
            stamp = os.stat(path).st_mtime_ns + 1_000_000
            os.utime(path, ns=(stamp, stamp))

    def test_missing_catalog_gives_404_null_block_and_zero_fields(self):
        tagged = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        self.assertIsNotNone({g["id"]: g for g in tagged["emit"]}["near-new"]["hls"])
        path = paths.catalog_db("CONUS")
        moved = path.with_name("moved.sqlite")
        path.rename(moved)
        try:
            for route in ("/api/missions/hls/tiles.geojson", "/api/missions/hls/counts?start=2025-07-15&end=2025-07-31"):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.get(route)
                self.assertEqual(ctx.exception.code, 404)
                self.assertIn("run.sh migrate", ctx.exception.read().decode())
            report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
            self.assertIsNone(report["missions"])
            for g in report["emit"]:
                self.assertNotIn("hls", g)
            catalog = json.loads(self.get("/api/catalog")[2])
            hls = {m["key"]: m for m in catalog["missions"]}["hls"]
            self.assertEqual((hls["count"], hls["tiles"], hls["fetched"]), (0, 0, None))
            self.assertFalse(catalog["catalog_busy"])
        finally:
            moved.rename(path)
```

Add `import os` at the top of the test file if missing. Any other test that reads `report["hls"]` (for example the busy-store test near line 660 of the current file) reads `report["missions"]` instead, with the same assertions. The tests that mutate the fixture catalog (`test_point_in_overlapping_tiles_lists_both`, `test_catalog_without_tiles_disables_the_layer`) bump the file's mtime so `catalog_for` reopens; they restore the fixture in `finally`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestMissionRoutes -v`
Expected: FAIL (404 on the new routes, `KeyError: 'missions'`).

- [ ] **Step 3: Rewrite the server's HLS parts**

In `viz/tileserver.py`:

1. Imports: replace `hls` in the `from viz import ...` line with `catalog, filters, registry` and add `from viz.archetypes import tiled`. Remove `HLS_SENSORS` and `HLS_BUSY`; add:

```python
CATALOG_BUSY = "catalog busy; a refresh is in progress, retry shortly"
NO_CATALOG = "no catalog; run ./run.sh migrate once, then ./run.sh refresh"
REGISTRY = None      # loaded by load_registry() at start; tests call it too


def load_registry(path=None):
    global REGISTRY
    REGISTRY = registry.load(path or paths.MISSIONS)
    return REGISTRY


def catalog_path():
    return paths.catalog_db(REGISTRY.region.name)


def catalog_read(work=None):
    """(value, busy) for one read of this thread's catalog, degraded instead of raised.

    work(entry) runs against the (Catalog, {grid: RingIndex}) pair. value is
    None when the catalog is absent or locked; busy tells those apart. Any
    OperationalError that is not a lock is a fault and propagates to the 500 handler.
    """
    try:
        entry = catalog.catalog_for(catalog_path())
        if entry is None:
            return None, False
        return (entry if work is None else work(entry)), False
    except catalog.BUSY as exc:
        if not catalog.is_busy(exc):
            raise
        return None, True
```

   Call `load_registry()` at module import time inside a `try` that lets `RegistryError` propagate only from `main()`: simplest is to call it at the top of `main()` and also lazily in `catalog_path()` (`if REGISTRY is None: load_registry()`), so the test harness, which imports the module and starts the server without `main()`, works too. In `main()`:

```python
    try:
        load_registry()
    except registry.RegistryError as exc:
        print(f"registry: {exc}", file=sys.stderr)
        return 2
```

2. Replace `hls_read` and `hls_block` with:

```python
def tiled_block(cat, indexes, mission, lon, lat, granules, start, end, values, window):
    """The point's tiles over [start, end] for a tiled mission, and a nearest-clear tag on each EMIT granule.

    granules are copies, never the index's own dicts. A granule's "hls" key is
    absent when no tile covers the point, None when no clear acquisition lies
    within the window. start and end are None without a range or week, and
    every tile then lists no acquisitions.
    """
    index = indexes.get(mission.grid)
    tiles = index.covering(lon, lat) if index else []
    block = {"start": start, "end": end, "cloud": values["cloud"], "sensor": values["sensor"], "window": window, "tiles": []}
    if not tiles:
        return block
    rows = tiled.acquisitions(cat, mission, tiles, start, end) if start and end else []
    for tile in tiles:
        acq = [{"date": r["date"], "time": r["time"], "sensor": r["sensor"], "cloud": r["cloud"]} for r in rows if r["tile"] == tile]
        clear = sum(1 for r in acq if filters.passes(r, mission, values))
        block["tiles"].append({"tile": tile, "clear": clear, "acq": acq})
    dated = [g for g in granules if g.get("start")]
    clear_rows = []
    if dated:
        first = min(g["start"][:10] for g in dated)
        last = max(g["start"][:10] for g in dated)
        lo = (datetime.date.fromisoformat(first) - datetime.timedelta(days=window)).isoformat()
        hi = (datetime.date.fromisoformat(last) + datetime.timedelta(days=window)).isoformat()
        clear_rows = [r for r in tiled.acquisitions(cat, mission, tiles, lo, hi) if filters.passes(r, mission, values)]
    for g in granules:
        g["hls"] = tiled.nearest_clear(clear_rows, g["start"][:10], window) if g.get("start") else None
    return block
```

3. In `point_report`, replace the `hls_read(lambda entry: hls_block(...))` call and the `"hls": hls_report` field with:

```python
    hls_mission = REGISTRY.missions.get("hls")
    values = {"cloud": params["cloud"], "sensor": params["sensor"]}
    missions_report, _ = catalog_read(lambda entry: {"hls": tiled_block(
        entry[0], entry[1], hls_mission, lon, lat, granules, params.get("start"), params.get("end"), values, params["window"])}) \
        if hls_mission else (None, False)
```

   and put `"missions": missions_report` in the returned dict in place of `"hls"`. (Step 4 generalizes this to every mission that is on; here only HLS exists as a tiled mission.)

4. `/api/catalog`: replace the four `hls_*` lines with:

```python
                def mission_rows(entry):
                    cat, indexes = entry
                    rows = []
                    for m in REGISTRY.missions.values():
                        summary = cat.summary(m.key)
                        rows.append({"key": m.key, "name": m.name, "label": m.label, "archetype": m.archetype,
                                     "grid": m.grid, "filters": filters.describe(m), "style": m.style,
                                     "count": summary["count"], "fetched": summary["fetched"],
                                     "tiles": indexes[m.grid].count if m.grid and m.grid in indexes else 0})
                    return rows
                missions, busy = catalog_read(mission_rows)
                if missions is None:
                    missions = [{"key": m.key, "name": m.name, "label": m.label, "archetype": m.archetype, "grid": m.grid,
                                 "filters": filters.describe(m), "style": m.style, "count": 0, "fetched": None, "tiles": 0}
                                for m in REGISTRY.missions.values()]
                catalog["missions"] = missions
                catalog["catalog_busy"] = busy
```

5. Routes: delete the `/api/hls/tiles.geojson` and `/api/hls/counts` branches and `_handle_hls_counts`; add before `/api/point`:

```python
            match = re.match(r"^/api/missions/([a-z][a-z0-9_]*)/(tiles\.geojson|counts)$", route)
            if match:
                return self._handle_mission(match.group(1), match.group(2), query)
```

   and the handler:

```python
    def _handle_mission(self, key, what, query):
        mission = REGISTRY.missions.get(key)
        if mission is None:
            return self._fail(HTTPStatus.NOT_FOUND, "unknown mission")
        if mission.archetype != "tiled":
            return self._fail(HTTPStatus.NOT_FOUND, f"mission {key} is not tiled")
        entry, busy = catalog_read()
        if busy:
            return self._fail(HTTPStatus.SERVICE_UNAVAILABLE, CATALOG_BUSY)
        if entry is None:
            return self._fail(HTTPStatus.NOT_FOUND, NO_CATALOG)
        if what == "tiles.geojson":
            geo, busy = catalog_read(lambda opened: self._tiles_bytes(opened[0], mission.grid))
            if busy:
                return self._fail(HTTPStatus.SERVICE_UNAVAILABLE, CATALOG_BUSY)
            return self._send(geo or b'{"type": "FeatureCollection", "features": []}', CONTENT_TYPES[".geojson"])
        params, message = self._range_params(query, required=True)
        if params is None:
            return self._fail(HTTPStatus.BAD_REQUEST, message)
        try:
            values = filters.parse(mission, query)
        except filters.FilterError as exc:
            return self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        counts, busy = catalog_read(lambda opened: tiled.counts(opened[0], mission, params["start"], params["end"], values))
        if busy:
            return self._fail(HTTPStatus.SERVICE_UNAVAILABLE, CATALOG_BUSY)
        self._send(json.dumps({"counts": counts or {}}).encode(), CONTENT_TYPES[".json"])

    @staticmethod
    def _tiles_bytes(cat, grid):
        cache = getattr(cat, "_tiles_bytes", None)
        if cache is None:
            cache = cat._tiles_bytes = {}
        if grid not in cache:
            cache[grid] = json.dumps(tiled.tiles_geojson(cat, grid)).encode()
        return cache[grid]
```

   Rename `_hls_params` to `_range_params` and drop its `sensor` handling (the point route still validates `cloud` and `window` there and reads `sensor` with the existing `HLS_SENSORS` check moved inline: keep a local tuple `("ALL", "L30", "S30")` in `_range_params` for the point route's `sensor` parameter until step 4, with a comment saying so).

6. Remove `import hls` uses: `grep -n "hls\." viz/tileserver.py` must print nothing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server -v 2>&1 | tail -15`
Expected: `TestMissionRoutes` passes; the static interface tests that assert `/api/hls/` in `app.js` still pass because the JavaScript is unchanged in this task (they change in Task 4). Then the full suite.

- [ ] **Step 5: Commit point**

Message: `Serve the HLS layer, counts, and readout block from the catalog`.

---

### Task 3: EMIT pairing from the catalog; retire the HLS fetch

**Files:**
- Modify: `viz/hls_pairs.py` (catalog + registry instead of `hls.Store`), `run.sh` (`hls` and `footprints` cases; the serve note for the HLS store), `tests/test_hls_pairs.py`
- Delete: `viz/fetch_hls.py`, `tests/test_fetch_hls.py`

**Interfaces:**
- Produces: `hls_pairs.pair(emit_features, cat, mission, index, days=PAIR_DAYS, cap=CAP) -> int` with the same output as today (centroid tiles from `index.covering`, acquisitions from `tiled.acquisitions`, one entry per date and sensor keeping the lowest cloud, nearest first, capped); `hls_pairs.main(argv=None)`: 1 with a message naming `./run.sh migrate` when the catalog is absent, 1 naming `./run.sh refresh hls` when the catalog has no `mgrs` tiles, 1 naming `./run.sh footprints` when the EMIT file is absent; otherwise pairs and rewrites the EMIT file.

- [ ] **Step 1: Rewrite the pairing tests**

Replace `tests/test_hls_pairs.py`'s fixture and store use: build a `catalog.Catalog` with `replace_month("hls", month, rows, fetched)` rows shaped `{id, start, end, tile, cloud, sensor}`, `put_tiles("mgrs", [...])`, an index `spatial.RingIndex((ring, tile) for tile, ring in cat.tile_rings("mgrs"))`, and the HLS mission from `registry.load()`; keep every existing assertion (nearest-first order, overlapping tiles merge, outside gives empty, cap and span parameters, main rewrites the file, main reports a missing catalog with `run.sh migrate`). Add:

```python
    def test_pairs_without_tiles_are_empty(self):
        empty = catalog.Catalog(self.tmp / "empty.sqlite")
        self.addCleanup(empty.close)
        empty.replace_month("hls", "2025-07", [], "x")
        index = spatial.RingIndex([])
        feature = emit_feature("inside", -93.5, 42.5, "2025-07-20T18:00:00Z")
        self.assertEqual(hls_pairs.pair([feature], empty, self.mission, index), 0)
        self.assertEqual(feature["properties"]["hls_near"], [])
        err = io.StringIO()
        with mock.patch.object(hls_pairs.paths, "EMIT_FOOTPRINTS", self.tmp / "e.geojson"), \
             mock.patch.object(hls_pairs.paths, "CATALOG_DATA", self.tmp), \
             contextlib.redirect_stderr(err):
            (self.tmp / "e.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
            code = hls_pairs.main([])
        self.assertEqual(code, 1)
        self.assertIn("run.sh refresh hls", err.getvalue())
```

   For that test `main` must find the empty catalog: `main` opens `paths.catalog_db(region)`, so the test writes its empty catalog at `self.tmp / "conus.sqlite"` (the region name lower-cased) rather than `empty.sqlite`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_hls_pairs -v`
Expected: FAIL (`pair` signature, `hls` import).

- [ ] **Step 3: Rewrite `viz/hls_pairs.py`**

```python
"""Attach the nearby HLS acquisitions to every EMIT scene.

Run after the HLS refresh by ./run.sh hls and ./run.sh footprints. For each
EMIT scene every MGRS tile containing the scene centroid is looked up (two
or four where tiles overlap), and their acquisitions within PAIR_DAYS either
side of the scene date are written onto the feature as its "hls_near"
property, nearest in time first, one entry per date and sensor with the
lowest cloud value kept. The interface marks ECOSTRESS + HLS coincidence
from this list without a round trip. Retired in step 4 of the mission
registry design, when coincidence becomes a query.
"""

import datetime
import json
import sys

from viz import catalog, cmr, paths, registry, spatial
from viz.archetypes import tiled

PAIR_DAYS = 15
CAP = 12


def centroid(ring):
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def pair(emit_features, cat, mission, index, days=PAIR_DAYS, cap=CAP):
    """Attach hls_near lists to every EMIT feature. Returns the number of scenes with at least one."""
    paired = 0
    span = datetime.timedelta(days=days)
    for feature in emit_features:
        props = feature["properties"]
        lon, lat = centroid(feature["geometry"]["coordinates"][0])
        tiles = index.covering(lon, lat)
        hits = []
        if tiles and props.get("start"):
            base = datetime.date.fromisoformat(props["start"][:10])
            rows = tiled.acquisitions(cat, mission, tiles, (base - span).isoformat(), (base + span).isoformat())
            best = {}
            for row in rows:
                key = (row["date"], row["sensor"])
                if key in best and (row["cloud"] is None or
                                    (best[key]["cloud"] is not None and best[key]["cloud"] <= row["cloud"])):
                    continue
                dt = (datetime.date.fromisoformat(row["date"]) - base).days
                best[key] = {"date": row["date"], "sensor": row["sensor"], "cloud": row["cloud"], "dt": dt, "_t": row["time"]}
            hits = sorted(best.values(), key=lambda h: (abs(h["dt"]), h["_t"]))
            for h in hits:
                del h["_t"]
        props["hls_near"] = hits[:cap]
        if hits:
            paired += 1
    return paired


def main(argv=None):
    reg = registry.load()
    mission = reg.missions.get("hls")
    path = paths.catalog_db(reg.region.name)
    if mission is None or not path.is_file():
        print(f"hls pairs: no catalog at {path}; run ./run.sh migrate once, then ./run.sh refresh", file=sys.stderr)
        return 1
    if not paths.EMIT_FOOTPRINTS.is_file():
        print(f"hls pairs: no EMIT file at {paths.EMIT_FOOTPRINTS}; run ./run.sh footprints first", file=sys.stderr)
        return 1
    cat = catalog.open_read_only(path)
    try:
        rings = cat.tile_rings(mission.grid)
        if not rings:
            print("hls pairs: the catalog has no HLS tile outlines; run ./run.sh refresh hls first", file=sys.stderr)
            return 1
        index = spatial.RingIndex((ring, tile) for tile, ring in rings)
        emit_data = json.loads(paths.EMIT_FOOTPRINTS.read_text())
        paired = pair(emit_data["features"], cat, mission, index)
    finally:
        cat.close()
    cmr.write_geojson(emit_data["features"], paths.EMIT_FOOTPRINTS)
    total = len(emit_data["features"])
    print(f"hls pairs: {paired} of {total} EMIT scenes have an HLS acquisition within "
          f"{PAIR_DAYS} days; lists written to {paths.EMIT_FOOTPRINTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Rewire `run.sh` and delete the fetch script**

In `run.sh`: the `hls)` case becomes

```bash
  hls)
    # Refreshes the HLS mission in the region catalog (frozen months skipped),
    # then pairs each EMIT scene with its tile's acquisitions within 15 days
    # for the coincidence marking. Accepts --from YYYY-MM. Network access.
    shift
    python3 -m viz.refresh hls "$@"
    if [ -f data/emit/footprints.geojson ]; then
      python3 -m viz.hls_pairs
    else
      echo "note: no EMIT footprints yet; run ./run.sh footprints to pair them with HLS" >&2
    fi
    ;;
```

   and in `footprints|emit)` replace `python3 -m viz.fetch_hls "$@"` with `python3 -m viz.refresh hls`. In the `serve)` case remove the `data/hls/hls.sqlite` note (the catalog note covers it). Delete `viz/fetch_hls.py` and `tests/test_fetch_hls.py` (`git rm` is a git write; delete the files with `rm` and leave the removal for the controller's commit). Check `bash -n run.sh`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_hls_pairs tests.test_run_sh -v`, then the full suite (the deleted test file's tests vanish; `tests/test_hls.py` still passes because `viz/hls.py` is untouched until Task 5). Update `tests/test_run_sh.py` if it asserts the old note text.

- [ ] **Step 6: Commit point**

Message: `Pair EMIT scenes with HLS from the catalog and retire the HLS fetch`.

---

### Task 4: The generated HLS block and the generic tiled layer

**Files:**
- Create: `viz/web/missions.js`
- Modify: `viz/web/app.js` (remove the HLS block; read HLS state through `missions.js`; readout reads `report.missions.hls`), `viz/web/index.html` (the HLS markup becomes `<div id="missionControls"></div>`; a `<script src="/static/missions.js">` before `app.js`), `viz/web/style.css` (generic selectors), `tests/test_server.py` (static and node asserts)

**Interfaces:**
- Produces, on a global `Missions` object defined by `missions.js` and used by `app.js`:
  - `Missions.init(map, catalog, hooks)` where `hooks` is `{ range: function () -> {start, end} | null, onFilterChange: function (key) }`: builds one block per catalog mission with `archetype === "tiled"` into `#missionControls`, in catalog order, and creates its layer state. Ids: checkbox `mission-<key>`, sub-controls `mission-<key>-controls`, a `max` filter slider `mission-<key>-f-<attr>` with output `mission-<key>-f-<attr>-out`, a `choice` filter radio group `name="mission-<key>-f-<attr>"`, mode radios `name="mission-<key>-mode"` (`week`, `season`), a range note `mission-<key>-range`, a legend `mission-<key>-legend`.
  - `Missions.isOn(key)`, `Missions.filter(key, attr)` (current value), `Missions.mode(key)`, `Missions.setAvailable(key, available, reason)`, `Missions.sync(key)` (load once, add or remove the layer, redraw the legend, debounced counts refetch through `/api/missions/<key>/counts?start&end&f.<attr>=…`), `Missions.syncAll()`, `Missions.refreshCounts(key)`, `Missions.drawLegend(key)`, `Missions.onTiles(key)` (true when the layer is on).
  - The tiled layer: pane `mission-<key>` at the mission's `style.pane` z-index (451 for HLS), Canvas renderer, the five-class purple ramp and outline colour as today (`TILED_CLASSES`, `TILED_OUTLINE` constants in `missions.js`), sticky tooltips `"<tile>: N clear"` with class `emit-tip`, the same style key set on every call.
  - The legend text and range note as today ("Window YYYY-MM-DD to YYYY-MM-DD", "Season …", "No CPC weeks for this selection", the class swatches and "clear acquisitions per MGRS tile").
- Consumes from `app.js`: `state.catalog`, `hlsRange()` renamed `tiledRange(key)` living in `app.js` (it depends on `weeksFor`, `weekSunday`, `state`), passed through `hooks.range` (the mode is read through `Missions.mode("hls")`).

- [ ] **Step 1: Update the static and node tests**

In `tests/test_server.py`:

1. The static HLS asserts (the test around line 1059 that asserts `id="hls"`, `name="hlsMode"`, `id="hlsRange"`, `id="hlsCloud"`, `name="hlsSensor"`, `id="hlsLegend"`, `/api/hls/tiles.geojson`, `/api/hls/counts?`) become `test_tiled_controls_are_generated`:

```python
    def test_tiled_controls_are_generated(self):
        _, _, index = self.get("/")
        html = index.decode()
        self.assertIn('id="missionControls"', html)
        self.assertIn('src="/static/missions.js"', html)
        self.assertLess(html.index('src="/static/missions.js"'), html.index('src="/static/app.js"'))
        for old in ('id="hls"', 'name="hlsMode"', 'id="hlsCloud"', 'name="hlsSensor"', 'id="hlsLegend"'):
            self.assertNotIn(old, html)
        _, _, missions = self.get("/static/missions.js")
        text = missions.decode()
        for needle in ('"mission-" + key', "-f-", "-mode", "-range", "-legend", "/api/missions/", "/tiles.geojson",
                       "/counts?start=", "f." , "TILED_CLASSES", "clear acquisitions per MGRS tile", "Missions.init"):
            self.assertIn(needle, text)
        _, _, app = self.get("/static/app.js")
        app_text = app.decode()
        self.assertNotIn("/api/hls/", app_text)
        self.assertNotIn("hlsCloud", app_text)
        self.assertIn('Missions.filter("hls", "cloud")', app_text)
        self.assertIn("report.missions", app_text)
```

2. The node runtime asserts: the `hlsPaired` snippet (around line 796) must define a stub `Missions` object instead of `state.hlsCloud`/`state.hlsSensor`: replace the prelude line with
   `'var HLS_PAIR_DAYS = 15; var state = { days: 7 }; var Missions = { filter: function (k, a) { return a === "cloud" ? 30 : "ALL"; } };\n'`
   and the later `state.hlsSensor = "L30"` line with `Missions.filter = function (k, a) { return a === "cloud" ? 30 : "L30"; }; out.push(hlsPaired(near));\n`, keeping the assertions. The `hlsRange` snippet (around line 1084) becomes `tiledRange("hls")` with a stub `var Missions = { mode: function () { return mode; } }; var mode = "week";` and `mode = "season"` in place of `state.hlsMode = "season"`. The assert at line 1162 (`state.hls && state.hlsMode === "week"`) becomes `Missions.isOn("hls") && Missions.mode("hls") === "week"`.
3. The `updateGroupTags` imagery list: the test that asserts sidebar tags (if any assert `["hls", "HLS"]`) changes to the generated id `mission-hls`.
4. Other existing asserts to update, by their current content: the sidebar test that lists `'id="hls"'` among `id="timeWindow"`, `id="emit"`, `id="eco"` (near line 896) replaces it with `'id="missionControls"'`; the pane asserts `map.createPane("hls")` and `451` after `map.getPane("hls")` (near lines 1069-1070) move to `missions.js`: assert `map.createPane("mission-" + key)` and `(spec.style && spec.style.pane) || 451` in the served `missions.js`; the assert `'state.hls && state.hlsMode === "week"'` (near line 1162) becomes `'Missions.isOn("hls") && Missions.mode("hls") === "week"'`; the assert `"no CPC weeks for this selection: nothing to count"` moves to the served `missions.js` (keep that comment text in `fetchCounts`); `foldable("hls"` (near line 1174) stays as is.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestInterfaceAssets -v 2>&1 | tail -12`
Expected: the new and changed asserts fail.

- [ ] **Step 3: Write `viz/web/missions.js`**

```js
// Registry-driven mission blocks in the sidebar and the generic tiled layer.
// app.js calls Missions.init(map, catalog, hooks) once the catalog is known;
// swath missions (EMIT, ECOSTRESS) stay hand-written in app.js until they migrate.
var Missions = (function () {
  "use strict";
  var TILED_CLASSES = [
    { min: 1, max: 1, colour: "#e7d4e8", label: "1" },
    { min: 2, max: 2, colour: "#c2a5cf", label: "2" },
    { min: 3, max: 4, colour: "#9970ab", label: "3–4" },
    { min: 5, max: 8, colour: "#762a83", label: "5–8" },
    { min: 9, max: Infinity, colour: "#40004b", label: "9+" }
  ];
  var TILED_OUTLINE = "#40004b";
  var COUNTS_DEBOUNCE_MS = 150;
  var map = null, catalog = null, hooks = null;
  var missions = {};        // key -> { spec, on, mode, filters, layer, loaded, counts, timer, request, renderer }

  function el(id) { return document.getElementById(id); }

  function classColour(n) {
    for (var i = 0; i < TILED_CLASSES.length; i++) {
      if (n >= TILED_CLASSES[i].min && n <= TILED_CLASSES[i].max) { return TILED_CLASSES[i].colour; }
    }
    return null;
  }

  function styleFor(counts) {
    return function (feature) {
      var n = counts[feature.properties.tile] || 0;
      var colour = classColour(n);
      return {
        stroke: true, interactive: true,
        color: TILED_OUTLINE, weight: 0.5, opacity: n ? 0.8 : 0.25,
        fill: !!colour, fillColor: colour || "#ffffff", fillOpacity: colour ? 0.55 : 0
      };
    };
  }

  function controlHtml(key, spec) {
    var html = '<label class="inline"><input autocomplete="off" type="checkbox" id="mission-' + key + '"> ' + spec.label + "</label>";
    html += '<div class="sub" id="mission-' + key + '-controls">';
    if (spec.archetype === "tiled") {
      html += '<div class="inline">Mode:' +
        '<label class="inline"><input autocomplete="off" type="radio" name="mission-' + key + '-mode" value="week" checked> Week window</label>' +
        '<label class="inline"><input autocomplete="off" type="radio" name="mission-' + key + '-mode" value="season"> Season</label></div>' +
        '<div class="note" id="mission-' + key + '-range"></div>';
    }
    spec.filters.forEach(function (f) {
      var id = "mission-" + key + "-f-" + f.attribute;
      if (f.control === "max") {
        html += "<label>" + f.label + ' <output id="' + id + '-out">' + f.default + "%</output>" +
          '<input autocomplete="off" type="range" id="' + id + '" min="0" max="100" step="5" value="' + f.default + '"></label>';
      } else {
        html += '<div class="inline">' + f.label + ":" + f.values.map(function (v) {
          return '<label class="inline"><input autocomplete="off" type="radio" name="' + id + '" value="' + v + '"' +
            (v === f.default ? " checked" : "") + "> " + (v === "ALL" ? "Both" : v) + "</label>";
        }).join("") + "</div>";
      }
    });
    html += '<div id="mission-' + key + '-legend"></div></div>';
    return html;
  }

  function checked(name) {
    var boxes = document.getElementsByName(name);
    for (var i = 0; i < boxes.length; i++) { if (boxes[i].checked) { return boxes[i].value; } }
    return null;
  }

  function filterQuery(key) {
    var m = missions[key];
    return Object.keys(m.filters).map(function (attr) { return "&f." + attr + "=" + encodeURIComponent(m.filters[attr]); }).join("");
  }

  function applyCounts(key) {
    var m = missions[key];
    if (!m.layer) { return; }
    m.layer.setStyle(styleFor(m.counts));
    m.layer.eachLayer(function (layer) {
      var tile = layer.feature.properties.tile;
      layer.setTooltipContent(tile + ": " + (m.counts[tile] || 0) + " clear");
    });
    drawLegend(key);
  }

  function fetchCounts(key) {
    var m = missions[key];
    var range = hooks.range(key);
    if (!m.layer || !m.on) { return; }
    if (!range) { m.request += 1; m.counts = {}; applyCounts(key); return; }
    var seq = ++m.request;
    fetch("/api/missions/" + key + "/counts?start=" + range.start + "&end=" + range.end + filterQuery(key))
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (seq !== m.request) { return; }
        m.counts = body.counts || {};
        applyCounts(key);
      })
      .catch(function () { /* keep the last counts */ });
  }

  function refreshCounts(key) {
    var m = missions[key];
    clearTimeout(m.timer);
    m.timer = setTimeout(function () { fetchCounts(key); }, COUNTS_DEBOUNCE_MS);
  }

  function load(key) {
    var m = missions[key];
    if (m.loaded || !(m.spec.count > 0 && m.spec.tiles > 0)) { return; }
    m.loaded = true;
    fetch("/api/missions/" + key + "/tiles.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      m.layer = L.geoJSON(geo, {
        pane: "mission-" + key, renderer: m.renderer, style: styleFor(m.counts),
        onEachFeature: function (f, layer) {
          layer.bindTooltip(f.properties.tile + ": 0 clear", { sticky: true, className: "emit-tip" });
        }
      });
      sync(key);
    }).catch(function () { m.loaded = false; });
  }

  function drawLegend(key) {
    var m = missions[key];
    var range = hooks.range(key);
    if (!m.on) { el("mission-" + key + "-range").textContent = ""; el("mission-" + key + "-legend").innerHTML = ""; return; }
    el("mission-" + key + "-range").textContent = range
      ? (m.mode === "season" ? "Season " : "Window ") + range.start + " to " + range.end
      : "No CPC weeks for this selection";
    el("mission-" + key + "-legend").innerHTML =
      '<span class="zero" style="--swatch:#fff">0</span>' +
      TILED_CLASSES.map(function (c) { return '<span style="--swatch:' + c.colour + '">' + c.label + "</span>"; }).join("") +
      "<span>clear acquisitions per MGRS tile</span>";
  }

  function setAvailable(key, available, reason) {
    var m = missions[key];
    var box = el("mission-" + key);
    box.disabled = !available;
    box.parentNode.title = available ? "" : reason;
    if (!available && m.on) { m.on = false; box.checked = false; }
    m.available = available;
  }

  function setControlsActive(key, active, modeActive) {
    el("mission-" + key + "-controls").classList.toggle("disabled", !active);
    var m = missions[key];
    m.spec.filters.forEach(function (f) {
      var id = "mission-" + key + "-f-" + f.attribute;
      if (f.control === "max") { el(id).disabled = !active; }
      else { Array.prototype.forEach.call(document.getElementsByName(id), function (r) { r.disabled = !active; }); }
    });
    Array.prototype.forEach.call(document.getElementsByName("mission-" + key + "-mode"), function (r) { r.disabled = !modeActive; });
  }

  function sync(key) {
    var m = missions[key];
    if (!m.on && m.layer && map.hasLayer(m.layer)) { map.removeLayer(m.layer); }
    if (!m.on) { drawLegend(key); return; }
    if (!m.layer) { load(key); return; }
    if (!map.hasLayer(m.layer)) { m.layer.addTo(map); }
    drawLegend(key);
    refreshCounts(key);
  }

  function wire(key) {
    var m = missions[key];
    el("mission-" + key).addEventListener("change", function (e) { m.on = e.target.checked; hooks.onToggle(key); });
    m.spec.filters.forEach(function (f) {
      var id = "mission-" + key + "-f-" + f.attribute;
      if (f.control === "max") {
        el(id).addEventListener("input", function (e) {
          m.filters[f.attribute] = Number(e.target.value);
          el(id + "-out").textContent = e.target.value + "%";
          refreshCounts(key);
          hooks.onFilterChange(key);
        });
      } else {
        Array.prototype.forEach.call(document.getElementsByName(id), function (radio) {
          radio.addEventListener("change", function (e) {
            if (!e.target.checked) { return; }
            m.filters[f.attribute] = e.target.value;
            drawLegend(key); refreshCounts(key); hooks.onFilterChange(key);
          });
        });
      }
    });
    Array.prototype.forEach.call(document.getElementsByName("mission-" + key + "-mode"), function (radio) {
      radio.addEventListener("change", function (e) {
        if (!e.target.checked) { return; }
        m.mode = e.target.value;
        drawLegend(key); refreshCounts(key); hooks.onFilterChange(key);
      });
    });
  }

  function init(theMap, theCatalog, theHooks) {
    map = theMap; catalog = theCatalog; hooks = theHooks;
    var container = el("missionControls");
    var html = "";
    (catalog.missions || []).forEach(function (spec) {
      if (spec.archetype !== "tiled") { return; }
      html += controlHtml(spec.key, spec);
    });
    container.innerHTML = html;
    (catalog.missions || []).forEach(function (spec) {
      if (spec.archetype !== "tiled") { return; }
      var key = spec.key;
      map.createPane("mission-" + key);
      map.getPane("mission-" + key).style.zIndex = (spec.style && spec.style.pane) || 451;
      var filters = {};
      spec.filters.forEach(function (f) { filters[f.attribute] = f.default; });
      missions[key] = { spec: spec, on: false, available: false, mode: "week", filters: filters, layer: null, loaded: false,
                        counts: {}, timer: null, request: 0, renderer: L.canvas({ pane: "mission-" + key }) };
      wire(key);
    });
  }

  return {
    init: init,
    keys: function () { return Object.keys(missions); },
    isOn: function (key) { return !!(missions[key] && missions[key].on); },
    isAvailable: function (key) { return !!(missions[key] && missions[key].available); },
    mode: function (key) { return missions[key] ? missions[key].mode : "week"; },
    filter: function (key, attr) { return missions[key] ? missions[key].filters[attr] : undefined; },
    setAvailable: setAvailable,
    setControlsActive: setControlsActive,
    sync: sync,
    syncAll: function () { Object.keys(missions).forEach(sync); },
    refreshCounts: refreshCounts,
    drawLegend: drawLegend
  };
})();
```

- [ ] **Step 4: Rewire `app.js`, `index.html`, and `style.css`**

`index.html`: replace the HLS label and `#hlsControls` block (the `<label ... id="hls">` line through the closing `</div>` of `hlsControls`) with `<div id="missionControls"></div>`; add `<script src="/static/missions.js"></script>` on the line before the `app.js` script tag. Update the time-window note if it names "the HLS week window" (keep the wording; it is still true).

`app.js`:
1. Remove `hls`, `hlsMode`, `hlsCloud`, `hlsSensor` from `state`; remove the `hls` pane, `hlsRenderer`, `HLS_CLASSES`, `HLS_OUTLINE`, `hlsLayer`, `hlsLoaded`, `hlsCounts`, `hlsTimer`, `hlsRequest`, `hlsColour`, `hlsStyleFor`, `applyHlsCounts`, `fetchHlsCounts`, `refreshHlsCounts`, `loadHls`, `drawHlsLegend`, and the body of `syncHls` except its availability logic, which becomes:

```js
  function syncHls() {
    // Availability comes from the catalog: rows without rings colour nothing, and a running
    // refresh holds the write lock, so both leave the layer off with the reason on the label.
    var spec = (state.catalog.missions || []).filter(function (m) { return m.key === "hls"; })[0];
    if (!spec) { return; }
    var busy = !!state.catalog.catalog_busy;
    var available = !busy && spec.count > 0 && spec.tiles > 0;
    Missions.setAvailable("hls", available,
      busy ? "Catalog busy; a refresh is in progress. Reload when it finishes."
      : spec.count > 0 ? "HLS tile outlines not computed yet; let ./run.sh refresh hls finish."
      : "No HLS rows in the catalog; run ./run.sh refresh hls");
    // Cloud and Sensor also drive the ECOSTRESS + HLS mark, so they stay live while that mark is on.
    var active = available && (Missions.isOn("hls") || state.coincideAll);
    Missions.setControlsActive("hls", active, available && Missions.isOn("hls"));
    Missions.sync("hls");
  }
```

2. `hlsPaired`: replace `state.hlsCloud` with `Missions.filter("hls", "cloud")` and `state.hlsSensor` with `Missions.filter("hls", "sensor")`.
3. `hlsRange()` becomes `tiledRange(key)` with `Missions.mode(key) === "season"` in place of `state.hlsMode`; every caller passes `"hls"` (the point-request URL builder and the time-window listener).
4. The readout: `var hlsBlock = report.missions && report.missions.hls;` replaces `report.hls`; the per-scene tag text keeps reading `g.hls` and `report.missions.hls.window`.
5. Listeners: delete the `el("hls")`, `el("hlsCloud")`, and the `["hlsMode", "hlsSensor"]` listeners; in the time-window listener replace `if (state.hls && state.hlsMode === "week") { drawHlsLegend(); refreshHlsCounts(); }` with `if (Missions.isOn("hls") && Missions.mode("hls") === "week") { Missions.drawLegend("hls"); Missions.refreshCounts("hls"); }`; the `coincideAll` listener keeps calling `syncHls()`.
6. In the catalog `then`: call `Missions.init(map, catalog, { range: tiledRange, onToggle: function () { syncHls(); }, onFilterChange: function () { if (state.coincideAll) { syncEmit(); } } })` right after `state.catalog = catalog;` and before `wire()`; replace `drawHlsLegend(); ... syncHls();` at the end with `syncHls();` (which now syncs the layer).
7. `updateGroupTags`: the imagery list entry `["hls", "HLS"]` becomes `["mission-hls", "HLS"]`.
8. Every remaining `state.hls` reference is gone: `grep -n "state.hls\|hlsLayer\|api/hls" viz/web/app.js` prints nothing.

`style.css`: change `#hlsControls` selectors to `[id^="mission-"][id$="-controls"]` (three rules), `#hlsLegend` to `[id^="mission-"][id$="-legend"]` (three rules), and `.leaflet-hls-pane` to `.leaflet-mission-hls-pane`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --check viz/web/missions.js && node --check viz/web/app.js && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestInterfaceAssets -v 2>&1 | tail -8`, then the full suite once. Start a probe server on port 8765, fetch `/`, `/static/missions.js`, `/static/app.js`, and `/api/catalog`, confirm the catalog's `missions` list and that the served index has `missionControls`, and kill the probe.

- [ ] **Step 6: Commit point**

Message: `Generate the HLS block from the registry and draw tiles through a generic layer`.

---

### Task 5: Retire the HLS store module and record the migration proof

**Files:**
- Delete: `viz/hls.py`, `tests/test_hls.py`
- Modify: `viz/paths.py` (remove `HLS_DATA`, `HLS_DB`), `viz/grids/mgrs.py` (no change; `hls.py` was the only re-exporter), `tests/test_months.py` and `tests/test_grids.py` (drop the `hls` re-export asserts), `tests/test_server.py` (any remaining `hls` import), `docs/superpowers/plans/2026-09-30-mission-registry-hls.md` (appendix)
- Test: the suite; a one-off comparison script in the scratchpad

**Interfaces:** none new.

- [ ] **Step 1: Prove old against new counts on the real data**

Before deleting anything, run in the scratchpad a script that opens the old store `data/hls/hls.sqlite` read-only through the still-present `viz.hls.Store` and the catalog through `viz.catalog.open_read_only`, and compares `counts` for three ranges (`2025-07-15..2025-07-31` cloud 30 ALL, `2025-03-01..2025-05-31` cloud 30 S30, `2024-01-01..2024-12-31` cloud 100 ALL) and `summary()["count"]` against the catalog's HLS `summary`. Expected: identical per-tile counts for the first two ranges apart from tiles touched by the September refresh (which happened after the migrate), and totals differing only by the granules that refresh added (about 4,000). Record the printed comparison in the appendix below.

- [ ] **Step 2: Remove the module and its references**

Delete `viz/hls.py` and `tests/test_hls.py`; remove `HLS_DATA` and `HLS_DB` from `viz/paths.py`; in `tests/test_months.py` delete `test_hls_re_exports_the_same_functions` and the `hls` import; in `tests/test_grids.py` replace the `hls.tile_ring` identity assert with an assert that `grids.mgrs.ring("T15TVH")` returns a five-vertex closed ring; `grep -rn "viz import.*hls\b\|from viz.hls\|paths.HLS_DB" viz tests` prints nothing. The old data files (`data/hls/hls.sqlite`) stay on disk; `migrate` still reads them.

- [ ] **Step 3: Run the suite and the serve check**

Run: `PYTHONNOUSERSITE=1 ./run.sh test 2>&1 | tail -3`; expected OK with the HLS store tests gone. Run `bash -n run.sh`.

- [ ] **Step 4: Commit point**

Message: `Retire the HLS store module`.

---

## Appendix: migration proof

Filled in by the executor after Task 5 step 1: the old-versus-new count comparison on the real data, with the ranges, the totals, and any tile whose counts differ and why.
