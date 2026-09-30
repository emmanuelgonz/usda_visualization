# Mission Registry Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the registry file, the catalog database, the shared spatial code, the grid modules, the two archetypes' fetch side, and the `refresh` and `migrate` commands in place, with the existing EMIT, ECOSTRESS, and HLS paths untouched and nothing visible changing.

**Architecture:** A JSON registry (`viz/missions.json`) validated by `viz/registry.py`; one SQLite catalog per region owned by `viz/catalog.py`; month arithmetic in `viz/months.py`; point-in-ring, box, and ring-intersection code in `viz/spatial.py` with a cell-bucketed index; `viz/grids/mgrs.py` holding the tile-ring maths; `viz/archetypes/swath.py` and `viz/archetypes/tiled.py` turning CMR pages into catalog rows; `viz/refresh.py` walking the registry into the catalog; `viz/migrate.py` importing the current stores. The old modules keep working by importing the moved functions from their new homes.

**Tech Stack:** Python 3.10 standard library (sqlite3, csv, json, re, urllib), GDAL/OGR bindings (osr for the MGRS transforms), unittest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-30-mission-registry-design.md` (this plan implements §9 step 1: Foundations; §2, §3, §4 fetch side, and the `migrate` and `refresh` commands).

## Global Constraints

- Every Python invocation is prefixed with `PYTHONNOUSERSITE=1`; the suite is `PYTHONNOUSERSITE=1 ./run.sh test` and must stay green with pristine output (no prints, no warnings) at every task.
- Standard library plus the GDAL/OGR bindings only.
- Nothing a user sees changes: `viz/tileserver.py`, `viz/web/`, and the three existing fetch commands keep working exactly as now.
- The registry file is `viz/missions.json`; the region is `{"name": "CONUS", "bbox": [-125.0, 24.4, -66.9, 49.4]}`; the catalog is `data/catalog/<region>.sqlite` (git-ignored under `data/`).
- Catalog schema exactly as spec §3: `granules`, `tiles`, `coverage`, `months` with the listed columns and indexes; `start`/`end` ISO UTC text.
- Read-only catalog connections use `file:<path>?mode=ro`, `uri=True`, `timeout=0.5`; a lock error is `catalog.BUSY` (`sqlite3.OperationalError`).
- Frozen rule: a month whose `months` row was written sixty days or more after the month ended is skipped.
- Tiled missions fetch CSV pages (`https://cmr.earthdata.nasa.gov/search/granules.csv`) by month with the `CMR-Hits` check and the under-delivery error; swath missions fetch JSON pages (`cmr.CMR_URL`) by month until an empty page.
- Commits are made by the controller after review; implementers leave work uncommitted. Never write attribution anywhere.

## Review Focus

1. A tiled CSV row whose `Browse URLs` cell holds several comma-separated URLs inside quotes: the row must parse and the first URL matching the registry's `browse.match` be stored (Task 6 test `test_browse_from_quoted_multi_url_cell`).
2. A swath entry whose polygon is unclosed or has fewer than four distinct points: the closed ring is stored, or the entry is skipped with a count, never a crash (Task 7 test `test_degenerate_polygon_is_skipped`).
3. A refresh whose fetch fails mid-month: the previous rows for that month stay and the `months` row is not updated, so the month is retried next run (Task 8 test `test_failed_month_keeps_previous_rows`).
4. `migrate` run twice: no duplicate granules, tiles, or coverage rows, and counts unchanged (Task 9 test `test_migrate_is_idempotent_and_computes_coverage`).
5. A registry pattern without a capture group for `tile_from`: rejected at load with the mission key, not at the first fetched row (Task 1 test `test_tile_from_pattern_needs_a_group`).

---

### Task 1: Registry file and loader

**Files:**
- Create: `viz/missions.json`
- Create: `viz/registry.py`
- Modify: `viz/paths.py` (add `MISSIONS`, `CATALOG_DATA`, `catalog_db`)
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces: `registry.load(path=paths.MISSIONS) -> Registry`; `Registry.region` is a `Region(name: str, bbox: tuple[float, float, float, float])`; `Registry.missions: dict[str, Mission]` in file order; `Registry.mission(key) -> Mission` (KeyError with the key when absent); `Registry.tiled() -> list[Mission]`, `Registry.swath() -> list[Mission]`; `Mission` attributes `key, name, label, archetype, cmr (list of Collection(short_name, version, implies: dict)), since, attributes (dict name -> Attribute(source, type)), filters (list of Filter(attribute, control, default, label, values)), browse (dict), links (dict), style (dict), footprint (swath only), grid (tiled only), tile_from (tiled only: TileFrom(field, pattern: re.Pattern))`; `Mission.grids_in_use` is not here (the refresh derives it from the registry). `registry.RegistryError(ValueError)`.
- Produces: `paths.MISSIONS = ROOT / "viz" / "missions.json"`, `paths.CATALOG_DATA = DATA / "catalog"`, `paths.catalog_db(region_name) -> Path` = `CATALOG_DATA / f"{region_name.lower()}.sqlite"`.

- [ ] **Step 1: Write the registry file**

Create `viz/missions.json`:

```json
{
  "version": 1,
  "region": {"name": "CONUS", "bbox": [-125.0, 24.4, -66.9, 49.4]},
  "missions": [
    {
      "key": "emit", "name": "EMIT", "label": "EMIT footprints",
      "archetype": "swath", "footprint": "polygon",
      "cmr": [{"short_name": "EMITL2ARFL", "version": "001"}],
      "since": "2022-08",
      "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}},
      "filters": [{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud cover"}],
      "browse": {"source": "links", "match": "\\.png$"},
      "links": {"data": {"rel": "data#", "match": "\\.nc$"}},
      "style": {"colour_by": "year", "pane": 455}
    },
    {
      "key": "eco", "name": "ECOSTRESS", "label": "ECOSTRESS swaths",
      "archetype": "swath", "footprint": "box",
      "cmr": [{"short_name": "ECO_L2_LSTE", "version": "002"}],
      "since": "2022-01",
      "attributes": {"daynight": {"from": "day_night_flag", "type": "text"},
                     "orbit": {"from": "orbit", "type": "int"}},
      "filters": [{"attribute": "daynight", "control": "choice", "values": ["DAY", "NIGHT", "ALL"], "default": "DAY", "label": "Time of day"}],
      "browse": {"source": "sibling", "short_name": "ECO_L2T_LSTE", "version": "002",
                 "id_pattern": "^ECOv002_L2_LSTE_(\\d+)_(\\d+)_",
                 "sibling_pattern": "ECOv002_L2T_LSTE_{0}_{1}_*",
                 "kinds": {"lst": "_LST.jpeg", "qc": "_QC.jpeg", "cloud": "_cloud.jpeg"}},
      "links": {},
      "style": {"colour": "#5b6770", "pane": 452}
    },
    {
      "key": "hls", "name": "HLS", "label": "HLS coverage",
      "archetype": "tiled", "grid": "mgrs",
      "cmr": [{"short_name": "HLSL30", "version": "2.0", "implies": {"sensor": "L30"}},
              {"short_name": "HLSS30", "version": "2.0", "implies": {"sensor": "S30"}}],
      "since": "2022-01",
      "tile_from": {"field": "title", "pattern": "^HLS\\.[LS]30\\.(T[0-9]{2}[A-Z]{3})\\."},
      "attributes": {"cloud": {"from": "cloud_cover", "type": "number"},
                     "sensor": {"from": "sensor", "type": "text"}},
      "filters": [{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud"},
                  {"attribute": "sensor", "control": "choice", "values": ["ALL", "L30", "S30"], "default": "ALL", "label": "Sensor"}],
      "browse": {"source": "links", "match": "\\.jpg$"},
      "links": {},
      "style": {"pane": 451}
    }
  ]
}
```

Note the HLS `tile_from` captures the tile with its leading `T` (`T15TVH`), matching the ids the HLS store and `grids.mgrs.ring` use.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_registry.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from viz import paths, registry

VALID = {
    "version": 1,
    "region": {"name": "Test", "bbox": [-100.0, 40.0, -99.0, 41.0]},
    "missions": [
        {"key": "sw", "name": "Swath", "label": "Swath scenes", "archetype": "swath", "footprint": "polygon",
         "cmr": [{"short_name": "SW", "version": "1"}], "since": "2024-01",
         "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}},
         "filters": [{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud"}],
         "browse": {"source": "links", "match": "\\.png$"}, "links": {}, "style": {"colour": "#123456"}},
        {"key": "ti", "name": "Tiled", "label": "Tiled coverage", "archetype": "tiled", "grid": "mgrs",
         "cmr": [{"short_name": "TA", "version": "2.0", "implies": {"sensor": "A"}}], "since": "2024-01",
         "tile_from": {"field": "title", "pattern": "^TI\\.(T[0-9]{2}[A-Z]{3})\\."},
         "attributes": {"sensor": {"from": "sensor", "type": "text"}},
         "filters": [{"attribute": "sensor", "control": "choice", "values": ["ALL", "A"], "default": "ALL", "label": "Sensor"}],
         "browse": {"source": "links", "match": "\\.jpg$"}, "links": {}, "style": {}},
    ],
}


def write(tmp, data):
    path = Path(tmp) / "missions.json"
    path.write_text(json.dumps(data))
    return path


class TestRealFile(unittest.TestCase):
    def test_real_registry_loads_with_three_missions(self):
        reg = registry.load(paths.MISSIONS)
        self.assertEqual(list(reg.missions), ["emit", "eco", "hls"])
        self.assertEqual(reg.region.name, "CONUS")
        self.assertEqual(reg.region.bbox, (-125.0, 24.4, -66.9, 49.4))
        self.assertEqual([m.key for m in reg.tiled()], ["hls"])
        self.assertEqual([m.key for m in reg.swath()], ["emit", "eco"])
        hls = reg.mission("hls")
        self.assertEqual(hls.tile_from.pattern.match("HLS.S30.T15TVH.2025203T170849.v2.0").group(1), "T15TVH")
        self.assertEqual(hls.cmr[0].implies, {"sensor": "L30"})
        self.assertEqual(reg.mission("eco").footprint, "box")
        self.assertEqual(reg.mission("emit").filters[0].default, 30)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def load(self, mutate):
        data = json.loads(json.dumps(VALID))
        mutate(data)
        return registry.load(write(self.tmp, data))

    def test_valid_fixture_loads(self):
        reg = self.load(lambda d: None)
        self.assertEqual(reg.mission("ti").attributes["sensor"].source, "sensor")
        with self.assertRaises(KeyError):
            reg.mission("nope")

    def test_duplicate_key_names_the_mission(self):
        with self.assertRaisesRegex(registry.RegistryError, "sw"):
            self.load(lambda d: d["missions"].append(dict(d["missions"][0])))

    def test_unknown_archetype_grid_footprint_control_and_source(self):
        for mutate, word in (
            (lambda d: d["missions"][0].update(archetype="orbit"), "archetype"),
            (lambda d: d["missions"][1].update(grid="hex"), "grid"),
            (lambda d: d["missions"][0].update(footprint="circle"), "footprint"),
            (lambda d: d["missions"][0]["filters"][0].update(control="range"), "control"),
            (lambda d: d["missions"][0]["browse"].update(source="ftp"), "browse"),
        ):
            with self.assertRaisesRegex(registry.RegistryError, word):
                self.load(mutate)

    def test_bad_pattern_and_filter_on_undeclared_attribute(self):
        with self.assertRaisesRegex(registry.RegistryError, "ti"):
            self.load(lambda d: d["missions"][1]["tile_from"].update(pattern="^TI\\.(T["))
        with self.assertRaisesRegex(registry.RegistryError, "cloud"):
            self.load(lambda d: d["missions"][1]["filters"].append(
                {"attribute": "cloud", "control": "max", "default": 1, "label": "x"}))

    def test_tile_from_pattern_needs_a_group(self):
        with self.assertRaisesRegex(registry.RegistryError, "group"):
            self.load(lambda d: d["missions"][1]["tile_from"].update(pattern="^TI\\.T[0-9]{2}[A-Z]{3}\\."))

    def test_since_must_be_a_month_and_tiled_needs_grid_and_tile_from(self):
        with self.assertRaisesRegex(registry.RegistryError, "since"):
            self.load(lambda d: d["missions"][0].update(since="2024-1"))
        with self.assertRaisesRegex(registry.RegistryError, "tile_from"):
            self.load(lambda d: d["missions"][1].pop("tile_from"))
        with self.assertRaisesRegex(registry.RegistryError, "footprint"):
            self.load(lambda d: d["missions"][0].pop("footprint"))

    def test_choice_default_must_be_a_value(self):
        with self.assertRaisesRegex(registry.RegistryError, "default"):
            self.load(lambda d: d["missions"][1]["filters"][0].update(default="Z"))


class TestPaths(unittest.TestCase):
    def test_catalog_db_is_lowercase_region_under_data_catalog(self):
        self.assertEqual(paths.catalog_db("CONUS"), paths.CATALOG_DATA / "conus.sqlite")
        self.assertEqual(paths.CATALOG_DATA, paths.DATA / "catalog")
        self.assertEqual(paths.MISSIONS, paths.ROOT / "viz" / "missions.json")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_registry -v`
Expected: FAIL with `ImportError: cannot import name 'registry'` (and `AttributeError` for `paths.MISSIONS`).

- [ ] **Step 4: Add the paths**

Append to `viz/paths.py` after the `HLS_DB` line:

```python
MISSIONS = ROOT / "viz" / "missions.json"      # the mission registry
CATALOG_DATA = DATA / "catalog"                 # one SQLite catalog per region


def catalog_db(region_name):
    """The catalog for a region, by its lower-cased name."""
    return CATALOG_DATA / f"{region_name.lower()}.sqlite"
```

- [ ] **Step 5: Write the loader**

Create `viz/registry.py`:

```python
"""The mission registry: viz/missions.json loaded and validated once.

Each mission names its CMR collections, its archetype (swath scenes with a
footprint each, or tiled acquisitions on a fixed grid), the attributes to keep
from CMR, the filters the sidebar offers, how to find a browse picture, and how
to draw it. Validation fails with the mission key in the message, so a bad
entry is caught at start rather than at the first fetched row.
"""

import json
import re
from dataclasses import dataclass, field

from viz import paths

ARCHETYPES = ("swath", "tiled")
FOOTPRINTS = ("polygon", "box")
GRIDS = ("mgrs",)
CONTROLS = ("max", "choice")
BROWSE_SOURCES = ("links", "sibling")
ATTRIBUTE_TYPES = ("number", "int", "text")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class RegistryError(ValueError):
    """The registry file is malformed; the message names the mission."""


@dataclass(frozen=True)
class Region:
    name: str
    bbox: tuple


@dataclass(frozen=True)
class Collection:
    short_name: str
    version: str
    implies: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Attribute:
    source: str
    type: str


@dataclass(frozen=True)
class Filter:
    attribute: str
    control: str
    default: object
    label: str
    values: tuple = ()


@dataclass(frozen=True)
class TileFrom:
    field: str
    pattern: re.Pattern


@dataclass(frozen=True)
class Mission:
    key: str
    name: str
    label: str
    archetype: str
    cmr: tuple
    since: str
    attributes: dict
    filters: tuple
    browse: dict
    links: dict
    style: dict
    footprint: str = None
    grid: str = None
    tile_from: TileFrom = None


class Registry:
    def __init__(self, region, missions):
        self.region = region
        self.missions = {m.key: m for m in missions}

    def mission(self, key):
        return self.missions[key]

    def tiled(self):
        return [m for m in self.missions.values() if m.archetype == "tiled"]

    def swath(self):
        return [m for m in self.missions.values() if m.archetype == "swath"]


def _fail(key, message):
    raise RegistryError(f"mission {key!r}: {message}")


def _compile(key, what, pattern, groups=0):
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        _fail(key, f"{what} pattern does not compile: {exc}")
    if compiled.groups < groups:
        _fail(key, f"{what} pattern needs {groups} capture group(s)")
    return compiled


def _mission(entry):
    key = entry.get("key")
    if not key or not re.match(r"^[a-z][a-z0-9_]*$", key):
        raise RegistryError(f"mission key {key!r} must be lower-case letters, digits, and underscores")
    archetype = entry.get("archetype")
    if archetype not in ARCHETYPES:
        _fail(key, f"unknown archetype {archetype!r}")
    since = entry.get("since", "")
    if not _MONTH_RE.match(since):
        _fail(key, f"since must be YYYY-MM, got {since!r}")
    collections = tuple(Collection(c["short_name"], str(c["version"]), dict(c.get("implies", {})))
                        for c in entry.get("cmr", []))
    if not collections:
        _fail(key, "needs at least one cmr collection")
    attributes = {}
    for name, spec in entry.get("attributes", {}).items():
        if spec.get("type") not in ATTRIBUTE_TYPES:
            _fail(key, f"attribute {name!r} has unknown type {spec.get('type')!r}")
        attributes[name] = Attribute(spec["from"], spec["type"])
    filters = []
    for spec in entry.get("filters", []):
        if spec.get("attribute") not in attributes:
            _fail(key, f"filter on undeclared attribute {spec.get('attribute')!r}")
        if spec.get("control") not in CONTROLS:
            _fail(key, f"unknown filter control {spec.get('control')!r}")
        values = tuple(spec.get("values", ()))
        if spec["control"] == "choice" and spec.get("default") not in values:
            _fail(key, f"choice default {spec.get('default')!r} is not one of {values}")
        filters.append(Filter(spec["attribute"], spec["control"], spec.get("default"), spec.get("label", ""), values))
    browse = dict(entry.get("browse", {}))
    if browse.get("source") not in BROWSE_SOURCES:
        _fail(key, f"unknown browse source {browse.get('source')!r}")
    if browse["source"] == "links":
        _compile(key, "browse match", browse.get("match", ""))
    else:
        for name in ("short_name", "version", "id_pattern", "sibling_pattern", "kinds"):
            if name not in browse:
                _fail(key, f"sibling browse needs {name!r}")
        _compile(key, "browse id", browse["id_pattern"])
    for name, spec in entry.get("links", {}).items():
        _compile(key, f"link {name!r} match", spec.get("match", ""))
    footprint = grid = tile_from = None
    if archetype == "swath":
        footprint = entry.get("footprint")
        if footprint not in FOOTPRINTS:
            _fail(key, f"swath needs a footprint of {FOOTPRINTS}, got {footprint!r}")
    else:
        grid = entry.get("grid")
        if grid not in GRIDS:
            _fail(key, f"unknown grid {grid!r}")
        spec = entry.get("tile_from")
        if not spec or "field" not in spec or "pattern" not in spec:
            _fail(key, "tiled needs tile_from with field and pattern")
        tile_from = TileFrom(spec["field"], _compile(key, "tile_from", spec["pattern"], groups=1))
    return Mission(key, entry.get("name", key), entry.get("label", key), archetype, collections, since,
                   attributes, tuple(filters), browse, dict(entry.get("links", {})), dict(entry.get("style", {})),
                   footprint, grid, tile_from)


def parse(data):
    """A Registry from the decoded file; RegistryError on any problem."""
    region = data.get("region") or {}
    bbox = region.get("bbox")
    if not region.get("name") or not isinstance(bbox, list) or len(bbox) != 4:
        raise RegistryError("region needs a name and a four-number bbox")
    missions = []
    seen = set()
    for entry in data.get("missions", []):
        mission = _mission(entry)
        if mission.key in seen:
            _fail(mission.key, "duplicate key")
        seen.add(mission.key)
        missions.append(mission)
    return Registry(Region(region["name"], tuple(float(v) for v in bbox)), missions)


def load(path=paths.MISSIONS):
    """The registry at path (default viz/missions.json)."""
    with open(path, encoding="utf-8") as handle:
        return parse(json.load(handle))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_registry -v`
Expected: PASS, 9 tests.

- [ ] **Step 7: Run the full suite**

Run: `PYTHONNOUSERSITE=1 ./run.sh test 2>&1 | tail -3`
Expected: OK, previous count plus 9.

- [ ] **Step 8: Commit point**

Message: `Add the mission registry file and its loader`. The controller commits after review.

---

### Task 2: Month arithmetic module

**Files:**
- Create: `viz/months.py`
- Modify: `viz/hls.py:97-134` (replace `_split_month`, `months_between`, `month_bounds`, `is_frozen` with imports)
- Test: `tests/test_months.py`

**Interfaces:**
- Produces: `months.months_between(first, last) -> list[str]`, `months.month_bounds(month) -> (start_iso, end_iso)`, `months.is_frozen(month, fetched_at, days=60) -> bool`, `months.current_month() -> "YYYY-MM"`, `months.now_iso() -> ISO UTC seconds`, `months.FROZEN_AFTER_DAYS = 60`. `viz.hls` keeps exposing the same three names by import, so `tests/test_hls.py` is unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_months.py`:

```python
import re
import unittest

from viz import hls, months


class TestMonths(unittest.TestCase):
    def test_between_bounds_and_frozen(self):
        self.assertEqual(months.months_between("2022-11", "2023-02"), ["2022-11", "2022-12", "2023-01", "2023-02"])
        self.assertEqual(months.month_bounds("2024-12"), ("2024-12-01", "2025-01-01"))
        self.assertFalse(months.is_frozen("2025-07", "2025-09-29T12:00:00+00:00"))
        self.assertTrue(months.is_frozen("2025-07", "2025-09-30T00:00:00Z"))

    def test_hls_re_exports_the_same_functions(self):
        self.assertIs(hls.months_between, months.months_between)
        self.assertIs(hls.month_bounds, months.month_bounds)
        self.assertIs(hls.is_frozen, months.is_frozen)

    def test_current_month_and_now_iso_shapes(self):
        self.assertRegex(months.current_month(), r"^\d{4}-\d{2}$")
        self.assertRegex(months.now_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_months -v`
Expected: FAIL with `ImportError: cannot import name 'months'`.

- [ ] **Step 3: Write the module and rewire hls**

Create `viz/months.py`:

```python
"""Month arithmetic shared by the catalog refresh and the HLS store.

Months are the unit of fetching because CMR caps paging depth at one million
rows per query; a month fetched long after it ended is frozen and never
fetched again.
"""

import datetime

FROZEN_AFTER_DAYS = 60


def _split_month(month):
    year, mon = month.split("-")
    return int(year), int(mon)


def months_between(first, last):
    """Every 'YYYY-MM' from first to last inclusive."""
    year, month = _split_month(first)
    last_year, last_month = _split_month(last)
    out = []
    while (year, month) <= (last_year, last_month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def month_bounds(month):
    """(first day of the month, first day of the next month) as ISO dates."""
    year, mon = _split_month(month)
    start = datetime.date(year, mon, 1)
    end = datetime.date(year + 1, 1, 1) if mon == 12 else datetime.date(year, mon + 1, 1)
    return start.isoformat(), end.isoformat()


def is_frozen(month, fetched_at, days=FROZEN_AFTER_DAYS):
    """True when the month was fetched at least `days` after it ended (calendar days in UTC)."""
    _, end = month_bounds(month)
    end_date = datetime.date.fromisoformat(end)
    stamp = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(datetime.timezone.utc)
    return (stamp.date() - end_date).days >= days


def current_month():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
```

In `viz/hls.py`, delete the definitions of `_split_month`, `months_between`, `month_bounds`, and `is_frozen` (lines 97 to 134) and add after the existing imports:

```python
from viz.months import FROZEN_AFTER_DAYS, is_frozen, month_bounds, months_between  # noqa: F401  (re-exported)
```

Remove the now-unused `FROZEN_AFTER_DAYS = 60` constant line in `hls.py` (the import supplies it). Keep `import datetime` in `hls.py` only if `nearest_clear` still uses it (it does).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_months tests.test_hls tests.test_fetch_hls -v`
Expected: PASS.

- [ ] **Step 5: Commit point**

Message: `Move month arithmetic into viz/months.py`.

---

### Task 3: Spatial module

**Files:**
- Create: `viz/spatial.py`
- Modify: `viz/emit.py:22-35` (replace the `point_in_ring` body with an import)
- Test: `tests/test_spatial.py`

**Interfaces:**
- Produces: `spatial.point_in_ring(lon, lat, ring) -> bool`; `spatial.ring_bbox(ring) -> (minlon, minlat, maxlon, maxlat)`; `spatial.boxes_intersect(a, b) -> bool` (closed intervals); `spatial.rings_intersect(a, b) -> bool` (any vertex of one inside the other, or any two edges crossing); `spatial.RingIndex(items, cell=1.0)` where `items` is an iterable of `(ring, payload)`; `RingIndex.covering(lon, lat) -> list[payload]` in insertion order; `RingIndex.intersecting(box) -> list[payload]` for rings whose bbox meets the box (bbox test only, callers refine with `rings_intersect`); `RingIndex.count`. `viz.emit.point_in_ring` stays importable and is the same function.

- [ ] **Step 1: Write the failing test**

Create `tests/test_spatial.py`:

```python
import unittest

from viz import emit, spatial

SQUARE = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
OFFSET = [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]            # overlaps SQUARE; its corner (1, 1) lies inside
FAR = [[10, 10], [11, 10], [11, 11], [10, 11], [10, 10]]
INSIDE = [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5], [0.5, 0.5]]


class TestPrimitives(unittest.TestCase):
    def test_point_in_ring_is_the_emit_function(self):
        self.assertIs(emit.point_in_ring, spatial.point_in_ring)
        self.assertTrue(spatial.point_in_ring(1, 1, SQUARE))
        self.assertFalse(spatial.point_in_ring(3, 1, SQUARE))

    def test_bbox_and_box_intersection(self):
        self.assertEqual(spatial.ring_bbox(OFFSET), (1, 1, 3, 3))
        self.assertTrue(spatial.boxes_intersect((0, 0, 2, 2), (2, 2, 3, 3)))     # touching corner counts
        self.assertFalse(spatial.boxes_intersect((0, 0, 2, 2), (2.1, 0, 3, 2)))

    def test_rings_intersect_by_vertex_containment_and_edge_crossing(self):
        self.assertTrue(spatial.rings_intersect(SQUARE, INSIDE))       # containment
        self.assertTrue(spatial.rings_intersect(INSIDE, SQUARE))
        self.assertTrue(spatial.rings_intersect(SQUARE, OFFSET))       # a vertex of each inside the other
        self.assertFalse(spatial.rings_intersect(SQUARE, FAR))
        cross_a = [[0, 1], [4, 1], [4, 1.5], [0, 1.5], [0, 1]]           # a thin bar through the square
        cross_b = [[1, -2], [1.5, -2], [1.5, 5], [1, 5], [1, -2]]        # a thin bar crossing it, no vertex inside
        self.assertTrue(spatial.rings_intersect(cross_a, cross_b))


class TestRingIndex(unittest.TestCase):
    def test_covering_and_intersecting_use_cells(self):
        index = spatial.RingIndex([(SQUARE, "square"), (FAR, "far"), (INSIDE, "inside")], cell=1.0)
        self.assertEqual(index.count, 3)
        self.assertEqual(index.covering(1, 1), ["square", "inside"])
        self.assertEqual(index.covering(10.5, 10.5), ["far"])
        self.assertEqual(index.covering(5, 5), [])
        self.assertEqual(index.intersecting((1.9, 1.9, 2.5, 2.5)), ["square"])
        self.assertEqual(index.intersecting((-5, -5, 20, 20)), ["square", "far", "inside"])

    def test_negative_coordinates_bucket_correctly(self):
        ring = [[-100.5, 40.5], [-99.5, 40.5], [-99.5, 41.5], [-100.5, 41.5], [-100.5, 40.5]]
        index = spatial.RingIndex([(ring, "r")])
        self.assertEqual(index.covering(-100.0, 41.0), ["r"])
        self.assertEqual(index.covering(-100.9, 41.0), [])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_spatial -v`
Expected: FAIL with `ImportError: cannot import name 'spatial'`.

- [ ] **Step 3: Write the module and rewire emit**

Create `viz/spatial.py`:

```python
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
    """True when the rings share area: a vertex of one inside the other, or two edges crossing."""
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
```

In `viz/emit.py`, delete the body of `point_in_ring` (lines 22 to 35) and replace the function with the import placed after the standard-library imports:

```python
from viz.spatial import point_in_ring  # noqa: F401  (re-exported; the index below and viz.hls use it)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_spatial tests.test_emit tests.test_basins tests.test_hls -v`
Expected: PASS.

- [ ] **Step 5: Commit point**

Message: `Add viz/spatial.py with a cell-bucketed ring index`.

---

### Task 4: Grid modules

**Files:**
- Create: `viz/grids/__init__.py`, `viz/grids/mgrs.py`
- Modify: `viz/hls.py:22-95` (replace the tile-ring block with an import)
- Test: `tests/test_grids.py`

**Interfaces:**
- Produces: `grids.get(name) -> module` with `ring(tile_id) -> closed [lon, lat] ring or None`; `grids.NAMES = ("mgrs",)`; `grids.mgrs.ring` is the former `hls.tile_ring`, and `hls.tile_ring` remains the same function object.

- [ ] **Step 1: Write the failing test**

Create `tests/test_grids.py`:

```python
import unittest

from viz import grids, hls


class TestGrids(unittest.TestCase):
    def test_mgrs_ring_is_the_hls_tile_ring(self):
        self.assertIs(grids.get("mgrs").ring, hls.tile_ring)
        self.assertEqual(grids.NAMES, ("mgrs",))
        ring = grids.mgrs.ring("T15TVH")
        self.assertEqual(len(ring), 5)
        self.assertEqual(ring[0], ring[-1])
        self.assertTrue(-95 < ring[0][0] < -92 and 40 < ring[0][1] < 43)   # central Iowa
        self.assertIsNone(grids.mgrs.ring("nonsense"))

    def test_unknown_grid_is_a_key_error(self):
        with self.assertRaises(KeyError):
            grids.get("hex")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_grids -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'viz.grids'`.

- [ ] **Step 3: Move the tile-ring code**

Create `viz/grids/mgrs.py` containing, verbatim from `viz/hls.py` lines 22 to 91: the `_TILE_ID_RE`, `_BANDS`, `_COLUMN_SETS`, `_ROW_LETTERS`, `TILE_SIDE_M`, `_transforms`, `_transforms_for`, and `tile_ring` definitions, with `tile_ring` renamed `ring` and a module docstring:

```python
"""The MGRS / Sentinel-2 / HLS tile grid: a tile's outline from its id.

A granule's CMR polygon is its data footprint, which at a swath edge is a
clipped piece of the tile, so outlines are computed from the id instead.
Northern hemisphere only, which covers every tile over CONUS.
"""

import re
```

(then the constants and functions; `ring(tile)` keeps `tile_ring`'s docstring and body).

Create `viz/grids/__init__.py`:

```python
"""Fixed tile grids for tiled missions, one module each exposing ring(tile_id)."""

from viz.grids import mgrs

NAMES = ("mgrs",)
_MODULES = {"mgrs": mgrs}


def get(name):
    """The grid module for a registry grid name; KeyError for an unknown one."""
    return _MODULES[name]
```

In `viz/hls.py`, delete lines 22 to 91 (from the `# MGRS tile ID` comment through the end of `tile_ring`) and add after the imports:

```python
from viz.grids.mgrs import TILE_SIDE_M, ring as tile_ring  # noqa: F401  (re-exported)
```

Keep `import re` in `hls.py` for `_UR_RE`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_grids tests.test_hls tests.test_fetch_hls -v`
Expected: PASS (the existing `tile_ring` tests in `test_hls.py` still pass through the re-export).

- [ ] **Step 5: Commit point**

Message: `Move the MGRS tile geometry into viz/grids/mgrs.py`.

---

### Task 5: Catalog schema, connections, and writes

**Files:**
- Create: `viz/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces: `catalog.BUSY = sqlite3.OperationalError`; `catalog.SCHEMA`; `catalog.ATTRIBUTE_COLUMNS = ("cloud", "daynight", "sensor", "orbit")`; `class Catalog(path, read_only=False)` with `.conn` (row factory `sqlite3.Row`), `.close()`, `replace_month(mission, month, rows, fetched_at)`, `fetched_at(mission, month) -> str | None`, `known_tiles(grid) -> set[str]`, `put_tiles(grid, pairs)`, `distinct_tiles(mission) -> list[str]`, `tile_rings(grid) -> list[(tile, ring)]`, `uncovered(mission, grid) -> list[(id, box, ring)]` (swath granules with no coverage row for that grid), `put_coverage(rows)` with rows `(mission, id, grid, tile)`, `summary(mission) -> {"count", "fetched", "months"}`, `missions_present() -> list[str]`. A row for `replace_month` is a dict with keys `id, start, end, cloud, daynight, sensor, orbit, tile, minlon, minlat, maxlon, maxlat, ring (list or None), browse, data, attrs (dict or None)`; missing keys are stored NULL. `catalog.open_read_only(path) -> Catalog` raising `FileNotFoundError` when absent.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_catalog.py`:

```python
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from viz import catalog

RING = [[-100, 40], [-99, 40], [-99, 41], [-100, 41], [-100, 40]]


def swath_row(gid, start, **extra):
    row = {"id": gid, "start": start, "end": start, "minlon": -100, "minlat": 40, "maxlon": -99, "maxlat": 41,
           "ring": RING, "browse": f"https://x/{gid}.png"}
    row.update(extra)
    return row


def tiled_row(gid, start, tile, cloud, sensor):
    return {"id": gid, "start": start, "end": start, "tile": tile, "cloud": cloud, "sensor": sensor}


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "cat" / "test.sqlite"
        self.cat = catalog.Catalog(self.path)

    def tearDown(self):
        self.cat.close()
        shutil.rmtree(self.tmp)

    def test_creates_schema_and_parent_directory(self):
        names = {r[0] for r in self.cat.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"granules", "tiles", "coverage", "months"})
        indexes = {r[0] for r in self.cat.conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'granules_%'")}
        self.assertEqual(indexes, {"granules_mission_start", "granules_mission_tile", "granules_mission_lat"})

    def test_replace_month_twice_gives_the_same_rows_and_records_the_fetch(self):
        rows = [swath_row("a", "2025-07-02T10:00:00Z", cloud=5.0), swath_row("b", "2025-07-30T10:00:00Z")]
        self.cat.replace_month("sw", "2025-07", rows, "2025-08-01T00:00:00+00:00")
        self.cat.replace_month("sw", "2025-07", rows, "2025-08-02T00:00:00+00:00")
        got = self.cat.conn.execute("SELECT id, cloud, ring, attrs FROM granules WHERE mission='sw' ORDER BY id").fetchall()
        self.assertEqual([(r["id"], r["cloud"]) for r in got], [("a", 5.0), ("b", None)])
        self.assertEqual(got[0]["ring"], '[[-100, 40], [-99, 40], [-99, 41], [-100, 41], [-100, 40]]')
        self.assertIsNone(got[0]["attrs"])
        self.assertEqual(self.cat.fetched_at("sw", "2025-07"), "2025-08-02T00:00:00+00:00")
        self.assertIsNone(self.cat.fetched_at("sw", "2025-08"))
        self.assertEqual(self.cat.summary("sw"), {"count": 2, "fetched": "2025-08-02T00:00:00+00:00", "months": 1})

    def test_replace_month_only_touches_that_mission_and_month(self):
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z")], "x")
        self.cat.replace_month("sw", "2025-08", [swath_row("c", "2025-08-02T10:00:00Z")], "x")
        self.cat.replace_month("ti", "2025-07", [tiled_row("t1", "2025-07-05T00:00:00Z", "T15TVH", 10, "A")], "x")
        self.cat.replace_month("sw", "2025-07", [], "y")
        ids = [r[0] for r in self.cat.conn.execute("SELECT id FROM granules ORDER BY id")]
        self.assertEqual(ids, ["c", "t1"])
        self.assertEqual(self.cat.missions_present(), ["sw", "ti"])

    def test_extra_attributes_go_to_attrs_json(self):
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z", attrs={"swir": 1})], "x")
        self.assertEqual(self.cat.conn.execute("SELECT attrs FROM granules").fetchone()[0], '{"swir": 1}')

    def test_tiles_and_coverage(self):
        self.cat.replace_month("ti", "2025-07", [tiled_row("t1", "2025-07-05T00:00:00Z", "T15TVH", 10, "A"),
                                                  tiled_row("t2", "2025-07-06T00:00:00Z", "T15TVG", 10, "A")], "x")
        self.assertEqual(self.cat.distinct_tiles("ti"), ["T15TVG", "T15TVH"])
        self.assertEqual(self.cat.known_tiles("mgrs"), set())
        self.cat.put_tiles("mgrs", [("T15TVH", RING)])
        self.assertEqual(self.cat.known_tiles("mgrs"), {"T15TVH"})
        self.assertEqual(self.cat.tile_rings("mgrs"), [("T15TVH", RING)])
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z")], "x")
        self.assertEqual([u[0] for u in self.cat.uncovered("sw", "mgrs")], ["a"])
        self.assertEqual(self.cat.uncovered("sw", "mgrs")[0][1], (-100.0, 40.0, -99.0, 41.0))
        self.cat.put_coverage([("sw", "a", "mgrs", "T15TVH")])
        self.cat.put_coverage([("sw", "a", "mgrs", "T15TVH")])          # idempotent
        self.assertEqual(self.cat.uncovered("sw", "mgrs"), [])
        self.assertEqual(self.cat.conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0], 1)

    def test_read_only_open_and_busy(self):
        self.cat.replace_month("sw", "2025-07", [swath_row("a", "2025-07-02T10:00:00Z")], "x")
        ro = catalog.open_read_only(self.path)
        try:
            self.assertEqual(ro.summary("sw")["count"], 1)
            with self.assertRaises(sqlite3.OperationalError):
                ro.conn.execute("INSERT INTO months VALUES ('z', '2025-01', 0, 'x')")
        finally:
            ro.close()
        with self.assertRaises(FileNotFoundError):
            catalog.open_read_only(self.tmp / "missing.sqlite")
        # a writer holding the lock makes a read-only read raise BUSY within the short timeout
        writer = sqlite3.connect(str(self.path), isolation_level=None)
        writer.execute("BEGIN EXCLUSIVE")
        try:
            ro = catalog.open_read_only(self.path)
            try:
                with self.assertRaises(catalog.BUSY):
                    ro.summary("sw")
            finally:
                ro.close()
        finally:
            writer.execute("ROLLBACK")
            writer.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_catalog -v`
Expected: FAIL with `ImportError: cannot import name 'catalog'`.

- [ ] **Step 3: Write the module**

Create `viz/catalog.py`:

```python
"""The per-region catalog: one SQLite file holding every mission's granules.

Written by viz/refresh.py and viz/migrate.py; read by the server through
short-timeout read-only connections that raise BUSY while a refresh commits,
so a route can degrade instead of stalling. Schema per the mission registry
design, section 3.
"""

import json
import sqlite3
from pathlib import Path

from viz.months import month_bounds

BUSY = sqlite3.OperationalError
ATTRIBUTE_COLUMNS = ("cloud", "daynight", "sensor", "orbit")
_ROW_COLUMNS = ("id", "start", "end", "cloud", "daynight", "sensor", "orbit", "tile",
                "minlon", "minlat", "maxlon", "maxlat", "ring", "browse", "data", "attrs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS granules (
  mission TEXT NOT NULL,
  id TEXT NOT NULL,
  start TEXT NOT NULL,
  end TEXT,
  cloud REAL,
  daynight TEXT,
  sensor TEXT,
  orbit INTEGER,
  tile TEXT,
  minlon REAL, minlat REAL, maxlon REAL, maxlat REAL,
  ring TEXT,
  browse TEXT,
  data TEXT,
  attrs TEXT,
  PRIMARY KEY (mission, id)
);
CREATE INDEX IF NOT EXISTS granules_mission_start ON granules(mission, start);
CREATE INDEX IF NOT EXISTS granules_mission_tile ON granules(mission, tile, start, cloud);
CREATE INDEX IF NOT EXISTS granules_mission_lat ON granules(mission, minlat, maxlat, start);
CREATE TABLE IF NOT EXISTS tiles (
  grid TEXT NOT NULL,
  tile TEXT NOT NULL,
  ring TEXT NOT NULL,
  PRIMARY KEY (grid, tile)
);
CREATE TABLE IF NOT EXISTS coverage (
  mission TEXT NOT NULL,
  id TEXT NOT NULL,
  grid TEXT NOT NULL,
  tile TEXT NOT NULL,
  PRIMARY KEY (mission, id, grid, tile)
);
CREATE INDEX IF NOT EXISTS coverage_grid_tile ON coverage(grid, tile, mission);
CREATE TABLE IF NOT EXISTS months (
  mission TEXT NOT NULL,
  month TEXT NOT NULL,
  count INTEGER NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (mission, month)
);
"""


def _values(mission, row):
    out = [mission]
    for column in _ROW_COLUMNS:
        value = row.get(column)
        if column in ("ring", "attrs") and value is not None:
            value = json.dumps(value)
        out.append(value)
    return out


class Catalog:
    """One connection to a region's catalog; writable by default, read-only for the server."""

    def __init__(self, path, read_only=False):
        self.path = Path(path)
        if read_only:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=0.5)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path))
            self.conn.executescript(SCHEMA)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    # --- writes ---

    def replace_month(self, mission, month, rows, fetched_at):
        """Replace one mission-month in a single transaction and record the fetch."""
        start, end = month_bounds(month)
        marks = ",".join("?" * (len(_ROW_COLUMNS) + 1))
        with self.conn:
            self.conn.execute("DELETE FROM granules WHERE mission = ? AND start >= ? AND start < ?",
                              (mission, start, end))
            self.conn.executemany(
                f"INSERT OR REPLACE INTO granules (mission, {', '.join(_ROW_COLUMNS)}) VALUES ({marks})",
                [_values(mission, r) for r in rows])
            self.conn.execute(
                "INSERT OR REPLACE INTO months (mission, month, count, fetched_at) VALUES (?, ?, ?, ?)",
                (mission, month, len(rows), fetched_at))

    def put_tiles(self, grid, pairs):
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO tiles (grid, tile, ring) VALUES (?, ?, ?)",
                                  [(grid, tile, json.dumps(ring)) for tile, ring in pairs])

    def put_coverage(self, rows):
        with self.conn:
            self.conn.executemany("INSERT OR IGNORE INTO coverage (mission, id, grid, tile) VALUES (?, ?, ?, ?)", rows)

    # --- reads ---

    def fetched_at(self, mission, month):
        row = self.conn.execute("SELECT fetched_at FROM months WHERE mission = ? AND month = ?",
                                (mission, month)).fetchone()
        return row["fetched_at"] if row else None

    def distinct_tiles(self, mission):
        return [r["tile"] for r in self.conn.execute(
            "SELECT DISTINCT tile FROM granules WHERE mission = ? AND tile IS NOT NULL ORDER BY tile", (mission,))]

    def known_tiles(self, grid):
        return {r["tile"] for r in self.conn.execute("SELECT tile FROM tiles WHERE grid = ?", (grid,))}

    def tile_rings(self, grid):
        return [(r["tile"], json.loads(r["ring"])) for r in self.conn.execute(
            "SELECT tile, ring FROM tiles WHERE grid = ? ORDER BY tile", (grid,))]

    def uncovered(self, mission, grid):
        """(id, box, ring) of the mission's granules with no coverage row for the grid."""
        sql = ("SELECT g.id, g.minlon, g.minlat, g.maxlon, g.maxlat, g.ring FROM granules g "
               "WHERE g.mission = ? AND g.ring IS NOT NULL AND NOT EXISTS ("
               "SELECT 1 FROM coverage c WHERE c.mission = g.mission AND c.id = g.id AND c.grid = ?) ORDER BY g.id")
        return [(r["id"], (r["minlon"], r["minlat"], r["maxlon"], r["maxlat"]), json.loads(r["ring"]))
                for r in self.conn.execute(sql, (mission, grid))]

    def summary(self, mission):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(count), 0) AS n, MAX(fetched_at) AS f, COUNT(*) AS m FROM months WHERE mission = ?",
            (mission,)).fetchone()
        return {"count": row["n"], "fetched": row["f"], "months": row["m"]}

    def missions_present(self):
        return [r["mission"] for r in self.conn.execute("SELECT DISTINCT mission FROM granules ORDER BY mission")]


def open_read_only(path):
    """A read-only Catalog; FileNotFoundError when the file is absent (never create one by accident)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return Catalog(path, read_only=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_catalog -v`
Expected: PASS, 6 tests. If the busy test passes without raising, confirm the writer used `BEGIN EXCLUSIVE` (a shared lock would still allow reads).

- [ ] **Step 5: Commit point**

Message: `Add the per-region catalog schema and writer`.

---

### Task 6: Tiled archetype, fetch side

**Files:**
- Create: `viz/archetypes/__init__.py`, `viz/archetypes/tiled.py`
- Test: `tests/test_archetype_tiled.py`

**Interfaces:**
- Consumes: `registry.Mission` (Task 1), `months.month_bounds` (Task 2), `cmr.PAGE_SIZE`, `cmr.fetch_response`.
- Produces: `tiled.CSV_URL`; `tiled.csv_url(collection, bbox, month, page_num) -> str`; `tiled.parse_csv(mission, collection, text) -> list[row]` (catalog rows: `id`, `start`, `end`, `tile`, attribute columns from the CSV and `implies`, `browse`); `tiled.fetch_month(mission, collection, bbox, month, fetch_fn=cmr.fetch_response) -> list[row]` with the hits check and under-delivery error; `archetypes.get(name) -> module` with `NAMES = ("swath", "tiled")` (the `swath` module arrives in Task 7; `get` imports lazily).

CSV columns (from CMR): `Granule UR, Producer Granule ID, Start Time, End Time, Online Access URLs, Browse URLs, Cloud Cover, Day/Night, Size`. Attribute sources for the tiled archetype: `cloud_cover` → `Cloud Cover` (number, blank → None), `day_night_flag` → `Day/Night`, and any name in the collection's `implies` map; `browse` is the first URL in `Browse URLs` (a quoted comma-separated cell) matching `mission.browse["match"]` when the source is `links`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_archetype_tiled.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from viz import archetypes, cmr, registry
from viz.archetypes import tiled

HEADER = "Granule UR,Producer Granule ID,Start Time,End Time,Online Access URLs,Browse URLs,Cloud Cover,Day/Night,Size\n"
BBOX = (-125.0, 24.4, -66.9, 49.4)

MISSION = registry.parse({
    "region": {"name": "T", "bbox": list(BBOX)},
    "missions": [{
        "key": "hls", "name": "HLS", "label": "HLS", "archetype": "tiled", "grid": "mgrs",
        "cmr": [{"short_name": "HLSL30", "version": "2.0", "implies": {"sensor": "L30"}},
                {"short_name": "HLSS30", "version": "2.0", "implies": {"sensor": "S30"}}],
        "since": "2022-01",
        "tile_from": {"field": "title", "pattern": "^HLS\\.[LS]30\\.(T[0-9]{2}[A-Z]{3})\\."},
        "attributes": {"cloud": {"from": "cloud_cover", "type": "number"},
                       "sensor": {"from": "sensor", "type": "text"},
                       "daynight": {"from": "day_night_flag", "type": "text"}},
        "filters": [], "browse": {"source": "links", "match": "\\.jpg$"}, "links": {}, "style": {}}]
}).mission("hls")
S30 = MISSION.cmr[1]


def row(ur, start, cloud, browse=""):
    return f'{ur},x,{start},{start},https://a/x.tif,{browse},{cloud},DAY,1\n'


class patch_page_size:
    def __init__(self, size):
        self.size = size

    def __enter__(self):
        self.saved = cmr.PAGE_SIZE
        cmr.PAGE_SIZE = self.size

    def __exit__(self, *args):
        cmr.PAGE_SIZE = self.saved
        return False


class TestUrlAndParse(unittest.TestCase):
    def test_csv_url_is_bounded_to_the_month_and_region(self):
        url = tiled.csv_url(S30, BBOX, "2025-07", 3)
        self.assertTrue(url.startswith(tiled.CSV_URL + "?"))
        for part in ("short_name=HLSS30", "version=2.0", "bounding_box=-125.0,24.4,-66.9,49.4",
                     "temporal=2025-07-01T00:00:00Z,2025-08-01T00:00:00Z", "page_size=2000", "page_num=3"):
            self.assertIn(part, url)

    def test_parse_rows_with_tile_attributes_and_implied_sensor(self):
        text = HEADER + row("HLS.S30.T15TVH.2025203T170849.v2.0", "2025-07-22T17:08:49.000Z", "14", "https://a/x.jpg") \
                      + row("HLS.S30.T15TVH.2025204T170849.v2.0", "2025-07-23T17:08:49.000Z", "", "") \
                      + row("ECOv002_L2_LSTE_1_1_20250722T000000_0713_01", "2025-07-22T00:00:00Z", "1", "")
        rows = tiled.parse_csv(MISSION, S30, text)
        self.assertEqual([r["id"] for r in rows], ["HLS.S30.T15TVH.2025203T170849.v2.0", "HLS.S30.T15TVH.2025204T170849.v2.0"])
        first = rows[0]
        self.assertEqual((first["tile"], first["cloud"], first["sensor"], first["daynight"], first["browse"]),
                         ("T15TVH", 14.0, "S30", "DAY", "https://a/x.jpg"))
        self.assertEqual(first["start"], "2025-07-22T17:08:49.000Z")
        self.assertIsNone(rows[1]["cloud"])
        self.assertIsNone(rows[1]["browse"])

    def test_browse_from_quoted_multi_url_cell(self):
        text = HEADER + row("HLS.S30.T15TVH.2025203T170849.v2.0", "2025-07-22T17:08:49Z", "1",
                            '"https://a/x.tif,https://a/thumb.jpg,https://a/y.png"')
        self.assertEqual(tiled.parse_csv(MISSION, S30, text)[0]["browse"], "https://a/thumb.jpg")


class TestFetchMonth(unittest.TestCase):
    def test_pages_by_hits_and_filters_to_the_month(self):
        pages = {
            1: HEADER + row("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10)
                      + row("HLS.S30.T15TVH.2025200T170000.v2.0", "2025-07-19T17:00:00Z", 20),
            2: HEADER + row("HLS.S30.T15TVH.2025201T170000.v2.0", "2025-07-20T17:00:00Z", 30)
                      + row("HLS.S30.T15TVH.2025213T000000.v2.0", "2025-08-01T00:00:00Z", 0),
        }
        asked = []

        def fake(url):
            page = int(url.rsplit("page_num=", 1)[1])
            asked.append(page)
            return pages[page].encode(), {"CMR-Hits": "4"}

        with patch_page_size(2):
            rows = tiled.fetch_month(MISSION, S30, BBOX, "2025-07", fetch_fn=fake)
        self.assertEqual(asked, [1, 2])
        self.assertEqual([r["start"][:10] for r in rows], ["2025-07-18", "2025-07-19", "2025-07-20"])

    def test_missing_hits_header_and_short_delivery_raise(self):
        with self.assertRaisesRegex(ValueError, "CMR-Hits"):
            tiled.fetch_month(MISSION, S30, BBOX, "2025-07", fetch_fn=lambda url: (HEADER.encode(), {}))
        short = HEADER + row("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10)
        with self.assertRaisesRegex(ValueError, "delivered 1"):
            tiled.fetch_month(MISSION, S30, BBOX, "2025-07", fetch_fn=lambda url: (short.encode(), {"CMR-Hits": "2"}))

    def test_zero_hits_is_one_request(self):
        asked = []

        def fake(url):
            asked.append(url)
            return HEADER.encode(), {"CMR-Hits": "0"}

        self.assertEqual(tiled.fetch_month(MISSION, S30, BBOX, "2022-01", fetch_fn=fake), [])
        self.assertEqual(len(asked), 1)


class TestPackage(unittest.TestCase):
    def test_get_returns_the_module(self):
        self.assertIs(archetypes.get("tiled"), tiled)
        self.assertEqual(archetypes.NAMES, ("swath", "tiled"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_archetype_tiled -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'viz.archetypes'`.

- [ ] **Step 3: Write the package and the module**

Create `viz/archetypes/__init__.py`:

```python
"""The two mission archetypes: swath scenes with a footprint each, and tiled acquisitions on a grid.

Each module has the same fetch-side duties: build a month's request, parse
a page into catalog rows, and fetch a whole month. The serving duties arrive
as the missions migrate.
"""

import importlib

NAMES = ("swath", "tiled")


def get(name):
    """The archetype module for a registry archetype name; KeyError for an unknown one."""
    if name not in NAMES:
        raise KeyError(name)
    return importlib.import_module(f"viz.archetypes.{name}")
```

Create `viz/archetypes/tiled.py`:

```python
"""Tiled missions: one CSV query per collection-month, rows keyed by grid tile.

CSV is used because a tiled row needs only the id, time, cloud, and browse
link, and the CSV endpoint is a fifth the size of JSON per granule. CMR caps
paging depth at one million rows per query, so months are the unit.
"""

import csv
import io
import re

from viz import cmr
from viz.months import month_bounds

CSV_URL = "https://cmr.earthdata.nasa.gov/search/granules.csv"
_CSV_SOURCES = {"cloud_cover": "Cloud Cover", "day_night_flag": "Day/Night"}


def csv_url(collection, bbox, month, page_num):
    start, end = month_bounds(month)
    box = ",".join(str(v) for v in bbox)
    return (f"{CSV_URL}?short_name={collection.short_name}&version={collection.version}&bounding_box={box}"
            f"&temporal={start}T00:00:00Z,{end}T00:00:00Z&page_size={cmr.PAGE_SIZE}&page_num={page_num}")


def _number(text, kind):
    if text in (None, ""):
        return None
    try:
        return int(float(text)) if kind == "int" else float(text)
    except ValueError:
        return None


def _attribute(spec, record, collection):
    if spec.source in collection.implies:
        value = collection.implies[spec.source]
    else:
        value = record.get(_CSV_SOURCES.get(spec.source, spec.source))
    if spec.type in ("number", "int"):
        return _number(value, spec.type)
    return value or None


def _browse(mission, record):
    if mission.browse.get("source") != "links":
        return None
    match = re.compile(mission.browse["match"])
    for url in (record.get("Browse URLs") or "").split(","):
        url = url.strip()
        if url.startswith("http") and match.search(url):
            return url
    return None


def parse_csv(mission, collection, text):
    """Catalog rows from a CMR granules.csv body; rows without a tile or start time are dropped."""
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        title = record.get("Granule UR") or ""
        match = mission.tile_from.pattern.match(title)
        start = record.get("Start Time") or ""
        if match is None or len(start) < 10:
            continue
        row = {"id": title, "start": start, "end": record.get("End Time") or start,
               "tile": match.group(1), "browse": _browse(mission, record)}
        for name, spec in mission.attributes.items():
            row[name] = _attribute(spec, record, collection)
        rows.append(row)
    return rows


def fetch_month(mission, collection, bbox, month, fetch_fn=cmr.fetch_response):
    """Every row of one collection-month, paged by CMR-Hits, limited to the month's dates.

    Raises ValueError without a usable CMR-Hits header, or when the pages
    deliver fewer parsed rows than CMR-Hits promised, so a short page is never
    recorded as a completed fetch and frozen.
    """
    url = csv_url(collection, bbox, month, 1)
    body, headers = fetch_fn(url)
    try:
        hits = int(headers.get("CMR-Hits"))
    except (TypeError, ValueError):
        raise ValueError(f"no CMR-Hits header in the response for {url}") from None
    rows = parse_csv(mission, collection, body.decode("utf-8"))
    pages = -(-hits // cmr.PAGE_SIZE)
    for page in range(2, pages + 1):
        body, _ = fetch_fn(csv_url(collection, bbox, month, page))
        rows.extend(parse_csv(mission, collection, body.decode("utf-8")))
    if len(rows) < hits:
        raise ValueError(f"{collection.short_name} {month}: CMR reported {hits} granules but delivered {len(rows)}")
    start, end = month_bounds(month)
    return [r for r in rows if start <= r["start"][:10] < end]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_archetype_tiled -v`
Expected: PASS, 7 tests (the package test passes once Task 7's `swath.py` exists only if `get("swath")` is called; this test calls `get("tiled")` only).

- [ ] **Step 5: Commit point**

Message: `Add the tiled archetype's CSV fetch into catalog rows`.

---

### Task 7: Swath archetype, fetch side and coverage

**Files:**
- Create: `viz/archetypes/swath.py`
- Test: `tests/test_archetype_swath.py`

**Interfaces:**
- Consumes: `registry.Mission`, `cmr.CMR_URL`, `cmr.PAGE_SIZE`, `cmr.fetch_page`, `cmr.polygon_ring`, `spatial.ring_bbox`, `spatial.RingIndex`, `spatial.rings_intersect`, `catalog.Catalog.uncovered`, `catalog.Catalog.tile_rings`, `catalog.Catalog.put_coverage`.
- Produces: `swath.page_url(collection, bbox, month, page_num) -> str`; `swath.entry_to_row(mission, collection, entry) -> row | None` (None without a usable footprint); `swath.fetch_month(mission, collection, bbox, month, fetch_fn=cmr.fetch_page) -> list[row]` (JSON pages until an empty page); `swath.compute_coverage(cat, mission, grid) -> int` (rows written for the mission's uncovered granules against the grid's tiles).

Attribute sources for the swath archetype: any top-level CMR entry field by name (`cloud_cover`, `day_night_flag`), plus the derived `orbit` (first `orbit_calculated_spatial_domains[].start_orbit_number`) and names in `implies`. Links: `browse` is the first http href whose `rel` ends with `browse#` and whose href matches `browse.match` (source `links`); each entry in `links` is the first http href whose `rel` ends with its `rel` and matches its `match`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_archetype_swath.py`:

```python
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import catalog, registry
from viz.archetypes import swath

BBOX = (-125.0, 24.4, -66.9, 49.4)
REG = registry.parse({
    "region": {"name": "T", "bbox": list(BBOX)},
    "missions": [
        {"key": "emit", "name": "EMIT", "label": "EMIT", "archetype": "swath", "footprint": "polygon",
         "cmr": [{"short_name": "EMITL2ARFL", "version": "001"}], "since": "2022-08",
         "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}},
         "filters": [], "browse": {"source": "links", "match": "\\.png$"},
         "links": {"data": {"rel": "data#", "match": "\\.nc$"}}, "style": {}},
        {"key": "eco", "name": "ECOSTRESS", "label": "ECO", "archetype": "swath", "footprint": "box",
         "cmr": [{"short_name": "ECO_L2_LSTE", "version": "002"}], "since": "2022-01",
         "attributes": {"daynight": {"from": "day_night_flag", "type": "text"}, "orbit": {"from": "orbit", "type": "int"}},
         "filters": [], "browse": {"source": "sibling", "short_name": "X", "version": "1", "id_pattern": "^(\\d+)_(\\d+)",
                                   "sibling_pattern": "{0}_{1}", "kinds": {}}, "links": {}, "style": {}},
    ]})
EMIT, ECO = REG.mission("emit"), REG.mission("eco")

POLY = {"title": "EMIT_1", "time_start": "2025-07-02T16:00:00.000Z", "time_end": "2025-07-02T16:00:12.000Z",
        "cloud_cover": "12", "polygons": [["40 -100 40 -99 41 -99 41 -100 40 -100"]],
        "links": [{"rel": "http://esipfed.org/ns/fedsearch/1.1/browse#", "href": "https://x/EMIT_1.png"},
                  {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "s3://x/EMIT_1.nc"},
                  {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://x/EMIT_1.nc"}]}
BOX = {"title": "ECO_1", "time_start": "2025-07-02T16:03:00.000Z", "time_end": "2025-07-02T16:03:52.000Z",
       "day_night_flag": "DAY", "boxes": ["39 -101 42 -98"],
       "orbit_calculated_spatial_domains": [{"start_orbit_number": "39607"}], "links": []}


class TestEntries(unittest.TestCase):
    def test_polygon_entry_to_row(self):
        row = swath.entry_to_row(EMIT, EMIT.cmr[0], POLY)
        self.assertEqual(row["id"], "EMIT_1")
        self.assertEqual(row["ring"], [[-100, 40], [-99, 40], [-99, 41], [-100, 41], [-100, 40]])
        self.assertEqual((row["minlon"], row["minlat"], row["maxlon"], row["maxlat"]), (-100, 40, -99, 41))
        self.assertEqual(row["cloud"], 12.0)
        self.assertEqual(row["browse"], "https://x/EMIT_1.png")
        self.assertEqual(row["data"], "https://x/EMIT_1.nc")
        self.assertEqual(row["start"], "2025-07-02T16:00:00.000Z")

    def test_box_entry_to_row_with_orbit(self):
        row = swath.entry_to_row(ECO, ECO.cmr[0], BOX)
        self.assertEqual(row["ring"], [[-101, 39], [-98, 39], [-98, 42], [-101, 42], [-101, 39]])
        self.assertEqual((row["daynight"], row["orbit"], row["browse"]), ("DAY", 39607, None))

    def test_degenerate_polygon_is_skipped(self):
        self.assertIsNone(swath.entry_to_row(EMIT, EMIT.cmr[0], dict(POLY, polygons=[["40 -100 41 -99"]])))
        self.assertIsNone(swath.entry_to_row(EMIT, EMIT.cmr[0], dict(POLY, polygons=[])))
        self.assertIsNone(swath.entry_to_row(EMIT, EMIT.cmr[0], dict(POLY, time_start=None)))
        unclosed = dict(POLY, polygons=[["40 -100 40 -99 41 -99 41 -100"]])
        self.assertEqual(swath.entry_to_row(EMIT, EMIT.cmr[0], unclosed)["ring"][-1], [-100, 40])


class TestFetchMonth(unittest.TestCase):
    def test_pages_until_empty_and_filters_to_the_month(self):
        pages = {1: [POLY, dict(POLY, title="EMIT_2", time_start="2025-08-01T00:00:00Z")], 2: []}
        asked = []

        def fake(url):
            page = int(url.rsplit("page_num=", 1)[1])
            asked.append(url)
            return pages[page]

        rows = swath.fetch_month(EMIT, EMIT.cmr[0], BBOX, "2025-07", fetch_fn=fake)
        self.assertEqual([r["id"] for r in rows], ["EMIT_1"])
        self.assertEqual(len(asked), 2)
        self.assertIn("temporal=2025-07-01T00:00:00Z,2025-08-01T00:00:00Z", asked[0])
        self.assertIn("short_name=EMITL2ARFL", asked[0])
        self.assertIn("version=001", asked[0])
        self.assertIn("bounding_box=-125.0,24.4,-66.9,49.4", asked[0])


class TestCoverage(unittest.TestCase):
    def test_coverage_rows_for_uncovered_granules(self):
        tmp = Path(tempfile.mkdtemp())
        cat = catalog.Catalog(tmp / "c.sqlite")
        try:
            cat.replace_month("emit", "2025-07", [swath.entry_to_row(EMIT, EMIT.cmr[0], POLY)], "x")
            cat.put_tiles("mgrs", [("A", [[-100.5, 39.5], [-99.5, 39.5], [-99.5, 40.5], [-100.5, 40.5], [-100.5, 39.5]]),
                                   ("B", [[-98, 40], [-97, 40], [-97, 41], [-98, 41], [-98, 40]]),
                                   ("C", [[-99.2, 40.8], [-98.9, 40.8], [-98.9, 41.2], [-99.2, 41.2], [-99.2, 40.8]])])
            self.assertEqual(swath.compute_coverage(cat, EMIT, "mgrs"), 2)     # A overlaps, C overlaps the corner, B is clear
            tiles = [r[0] for r in cat.conn.execute("SELECT tile FROM coverage WHERE id='EMIT_1' ORDER BY tile")]
            self.assertEqual(tiles, ["A", "C"])
            self.assertEqual(swath.compute_coverage(cat, EMIT, "mgrs"), 0)     # nothing left uncovered
        finally:
            cat.close()
            shutil.rmtree(tmp)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_archetype_swath -v`
Expected: FAIL with `ImportError: cannot import name 'swath'`.

- [ ] **Step 3: Write the module**

Create `viz/archetypes/swath.py`:

```python
"""Swath missions: one JSON query per collection-month, one ring per scene.

JSON is needed because only the JSON endpoint carries the polygon or box.
Coverage against each tiled grid is computed once per granule so that
coincidence with a tiled partner is a join on tile and date.
"""

import re

from viz import cmr, spatial
from viz.months import month_bounds


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


def fetch_month(mission, collection, bbox, month, fetch_fn=cmr.fetch_page):
    """Every row of one collection-month, paging until an empty page, limited to the month's dates."""
    rows = []
    page = 1
    while True:
        entries = fetch_fn(page_url(collection, bbox, month, page))
        if not entries:
            break
        rows.extend(r for r in (entry_to_row(mission, collection, e) for e in entries) if r is not None)
        page += 1
    start, end = month_bounds(month)
    return [r for r in rows if start <= r["start"][:10] < end]


def compute_coverage(cat, mission, grid):
    """Write coverage rows for the mission's granules not yet covered against the grid; returns rows written."""
    tiles = cat.tile_rings(grid)
    if not tiles:
        return 0
    index = spatial.RingIndex((ring, (tile, ring)) for tile, ring in tiles)
    rows = []
    for gid, box, ring in cat.uncovered(mission.key, grid):
        for tile, tile_ring in index.intersecting(box):
            if spatial.rings_intersect(ring, tile_ring):
                rows.append((mission.key, gid, grid, tile))
    cat.put_coverage(rows)
    return len(rows)
```

Note `entry_to_row` stores extra declared links under their own names (for example `data`); the catalog keeps `data` as a column and would drop any other link name, so a mission declaring a link not named `data` must also declare it in `attrs` (not needed for the three missions; the registry review in project 2 revisits this).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_archetype_swath tests.test_archetype_tiled -v`
Expected: PASS.

- [ ] **Step 5: Commit point**

Message: `Add the swath archetype's JSON fetch and grid coverage`.

---

### Task 8: The refresh command

**Files:**
- Create: `viz/refresh.py`
- Modify: `run.sh` (add the `refresh` case and the usage line)
- Test: `tests/test_refresh.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `refresh.refresh_mission(cat, reg, mission, first=None, fetch_csv=None, fetch_json=None, out=sys.stdout, now=months.now_iso) -> dict` (a None fetcher resolves to `cmr.fetch_response` / `cmr.fetch_page` at call time so tests can patch the cmr module) with counts `{"months": n_fetched, "granules": n_rows, "tiles": n_new_tiles, "coverage": n_rows}`; `refresh.finish_grids(cat, reg, out) -> None` (rings for new tile ids of every tiled mission, then coverage for every swath mission against every grid in use); `refresh.main(argv=None) -> int` with `refresh [MISSION ...] [--from YYYY-MM] [--registry PATH] [--catalog PATH]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_refresh.py`:

```python
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import catalog, refresh, registry
from tests.test_archetype_swath import POLY, REG as SWATH_REG
from tests.test_archetype_tiled import HEADER, MISSION as HLS, row as csv_row

NOW = lambda: "2025-08-15T00:00:00+00:00"


class TestRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cat = catalog.Catalog(self.tmp / "c.sqlite")
        self.out = io.StringIO()

    def tearDown(self):
        self.cat.close()
        shutil.rmtree(self.tmp)

    def test_tiled_mission_fetches_each_collection_month_and_skips_frozen(self):
        reg = registry.Registry(SWATH_REG.region, [HLS])
        calls = []

        def fake_csv(url):
            calls.append(url)
            if "HLSS30" in url and "2025-07" in url:
                return (HEADER + csv_row("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10)).encode(), {"CMR-Hits": "1"}
            return HEADER.encode(), {"CMR-Hits": "0"}

        self.cat.replace_month("hls", "2025-06", [], "2025-08-01T00:00:00+00:00")   # 31 days after month end: not frozen
        self.cat.replace_month("hls", "2025-05", [], "2025-08-01T00:00:00+00:00")   # 62 days after: frozen
        with unittest.mock.patch("viz.months.current_month", return_value="2025-07"):
            counts = refresh.refresh_mission(self.cat, reg, HLS, first="2025-05", fetch_csv=fake_csv, out=self.out, now=NOW)
        months_asked = sorted({u.rsplit("temporal=", 1)[1][:7] for u in calls})
        self.assertEqual(months_asked, ["2025-06", "2025-07"])
        self.assertEqual(counts["months"], 2)
        self.assertEqual(counts["granules"], 1)
        self.assertEqual(self.cat.fetched_at("hls", "2025-07"), NOW())
        self.assertIn("hls 2025-07: 1 granules", self.out.getvalue())

    def test_failed_month_keeps_previous_rows(self):
        reg = registry.Registry(SWATH_REG.region, [HLS])
        self.cat.replace_month("hls", "2025-07", [{"id": "old", "start": "2025-07-01T00:00:00Z", "tile": "T15TVH"}], "2025-07-20T00:00:00+00:00")

        def failing(url):
            raise OSError("cmr down")

        with unittest.mock.patch("viz.months.current_month", return_value="2025-07"):
            with self.assertRaises(OSError):
                refresh.refresh_mission(self.cat, reg, HLS, first="2025-07", fetch_csv=failing, out=self.out, now=NOW)
        self.assertEqual([r[0] for r in self.cat.conn.execute("SELECT id FROM granules")], ["old"])
        self.assertEqual(self.cat.fetched_at("hls", "2025-07"), "2025-07-20T00:00:00+00:00")

    def test_finish_grids_computes_rings_and_coverage(self):
        reg = registry.Registry(SWATH_REG.region, [SWATH_REG.mission("emit"), HLS])
        self.cat.replace_month("hls", "2025-07", [{"id": "t", "start": "2025-07-01T00:00:00Z", "tile": "T15TVH", "sensor": "S30"}], "x")
        from viz.archetypes import swath
        emit = SWATH_REG.mission("emit")
        iowa = dict(POLY, polygons=[["41.5 -94 41.5 -93 42.5 -93 42.5 -94 41.5 -94"]])
        self.cat.replace_month("emit", "2025-07", [swath.entry_to_row(emit, emit.cmr[0], iowa)], "x")
        refresh.finish_grids(self.cat, reg, out=self.out)
        self.assertEqual(self.cat.known_tiles("mgrs"), {"T15TVH"})
        self.assertEqual([r[0] for r in self.cat.conn.execute("SELECT tile FROM coverage WHERE mission='emit'")], ["T15TVH"])
        self.assertIn("mgrs: 1 tile outlines", self.out.getvalue())
        self.assertIn("emit coverage: 1 rows", self.out.getvalue())

    def test_main_runs_named_missions_against_a_registry_and_catalog_path(self):
        reg_path = self.tmp / "m.json"
        reg_path.write_text(json.dumps({
            "region": {"name": "Test", "bbox": [-125.0, 24.4, -66.9, 49.4]},
            "missions": [{"key": "emit", "name": "EMIT", "label": "EMIT", "archetype": "swath", "footprint": "polygon",
                          "cmr": [{"short_name": "EMITL2ARFL", "version": "001"}], "since": "2025-07",
                          "attributes": {}, "filters": [], "browse": {"source": "links", "match": "\\.png$"},
                          "links": {}, "style": {}}]}))
        pages = {1: [POLY], 2: []}
        with unittest.mock.patch("viz.months.current_month", return_value="2025-07"), \
             unittest.mock.patch("viz.cmr.fetch_page", side_effect=lambda url: pages[int(url.rsplit("page_num=", 1)[1])]), \
             unittest.mock.patch("sys.stdout", self.out):
            code = refresh.main(["emit", "--registry", str(reg_path), "--catalog", str(self.tmp / "r.sqlite")])
        self.assertEqual(code, 0)
        cat = catalog.open_read_only(self.tmp / "r.sqlite")
        try:
            self.assertEqual(cat.summary("emit")["count"], 1)
        finally:
            cat.close()

    def test_unknown_mission_is_an_error(self):
        with unittest.mock.patch("sys.stderr", io.StringIO()) as err:
            code = refresh.main(["nope", "--catalog", str(self.tmp / "r.sqlite")])
        self.assertEqual(code, 2)
        self.assertIn("nope", err.getvalue())


import unittest.mock  # noqa: E402  (used above via unittest.mock)
```

Put `import unittest.mock` at the top with the other imports rather than at the bottom (the trailing line above is only to show it is required).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_refresh -v`
Expected: FAIL with `ImportError: cannot import name 'refresh'`.

- [ ] **Step 3: Write the command**

Create `viz/refresh.py`:

```python
"""Refresh the region catalog from CMR for every mission in the registry.

Run by ./run.sh refresh [MISSION ...]. For each mission and month since its
registry `since`, a frozen month is skipped, the mission's collections are
paged, and the month is replaced in one transaction. Then new tile ids get
outlines and swath granules get coverage rows against every grid in use.
"""

import argparse
import sys

from viz import archetypes, catalog, cmr, grids, months, paths, registry


def refresh_mission(cat, reg, mission, first=None, fetch_csv=None, fetch_json=None,
                    out=sys.stdout, now=months.now_iso):
    """Fetch the mission's non-frozen months into the catalog; returns counts.

    The fetchers default to the cmr module's functions at call time, so a test
    can patch viz.cmr.fetch_page and viz.cmr.fetch_response.
    """
    fetch_csv = fetch_csv or cmr.fetch_response
    fetch_json = fetch_json or cmr.fetch_page
    archetype = archetypes.get(mission.archetype)
    counts = {"months": 0, "granules": 0}
    for month in months.months_between(first or mission.since, months.current_month()):
        fetched = cat.fetched_at(mission.key, month)
        if fetched and months.is_frozen(month, fetched):
            continue
        rows = []
        for collection in mission.cmr:
            if mission.archetype == "tiled":
                rows.extend(archetype.fetch_month(mission, collection, reg.region.bbox, month, fetch_fn=fetch_csv))
            else:
                rows.extend(archetype.fetch_month(mission, collection, reg.region.bbox, month, fetch_fn=fetch_json))
        cat.replace_month(mission.key, month, rows, now())
        counts["months"] += 1
        counts["granules"] += len(rows)
        print(f"{mission.key} {month}: {len(rows)} granules", file=out, flush=True)
    return counts


def finish_grids(cat, reg, out=sys.stdout):
    """Outlines for new tile ids of every tiled mission, then coverage for every swath mission."""
    in_use = []
    for mission in reg.tiled():
        grid = grids.get(mission.grid)
        known = cat.known_tiles(mission.grid)
        outlines = []
        for tile in cat.distinct_tiles(mission.key):
            if tile in known:
                continue
            ring = grid.ring(tile)
            if ring is None:
                print(f"{mission.key}: cannot place tile {tile}; it is counted but not drawn", file=sys.stderr)
                continue
            outlines.append((tile, ring))
        cat.put_tiles(mission.grid, outlines)
        print(f"{mission.grid}: {len(outlines)} tile outlines computed", file=out, flush=True)
        if mission.grid not in in_use:
            in_use.append(mission.grid)
    swath = archetypes.get("swath")
    for mission in reg.swath():
        for grid_name in in_use:
            written = swath.compute_coverage(cat, mission, grid_name)
            print(f"{mission.key} coverage: {written} rows against {grid_name}", file=out, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh the region catalog from CMR.")
    parser.add_argument("missions", nargs="*", help="mission keys (default: every mission in the registry)")
    parser.add_argument("--from", dest="first", help="first month to fetch, YYYY-MM (default: each mission's since)")
    parser.add_argument("--registry", default=str(paths.MISSIONS))
    parser.add_argument("--catalog", help="catalog path (default: data/catalog/<region>.sqlite)")
    args = parser.parse_args(argv)

    reg = registry.load(args.registry)
    unknown = [k for k in args.missions if k not in reg.missions]
    if unknown:
        print(f"unknown mission(s): {', '.join(unknown)}; known: {', '.join(reg.missions)}", file=sys.stderr)
        return 2
    selected = [reg.mission(k) for k in args.missions] or list(reg.missions.values())
    cat = catalog.Catalog(args.catalog or paths.catalog_db(reg.region.name))
    try:
        for mission in selected:
            counts = refresh_mission(cat, reg, mission, first=args.first)
            print(f"{mission.key}: {counts['months']} months, {counts['granules']} granules", flush=True)
        finish_grids(cat, reg)
        for mission in selected:
            summary = cat.summary(mission.key)
            print(f"{mission.key}: {summary['count']} granules over {summary['months']} months in {cat.path}")
    finally:
        cat.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `run.sh`, add before the `serve)` case:

```bash
  refresh)
    # Refreshes the region catalog (data/catalog/<region>.sqlite) from CMR for
    # every mission in viz/missions.json, or the named ones; frozen months are
    # skipped, then tile outlines and swath coverage are computed. Network access.
    shift
    exec python3 -m viz.refresh "$@"
    ;;
```

and change the usage line to `{extract|prepare|vendor|footprints|emit|hls|refresh|migrate|serve|test}` (the `migrate` case arrives in Task 9). Check with `bash -n run.sh`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_refresh -v`
Expected: PASS, 5 tests. The `unittest.mock.patch("viz.months.current_month")` patches the module attribute that `refresh_mission` reads through `months.current_month()`; if the test finds the real current month instead, check that `refresh.py` calls `months.current_month()` rather than importing the function by name.

- [ ] **Step 5: Commit point**

Message: `Add the catalog refresh command`.

---

### Task 9: The migrate command

**Files:**
- Create: `viz/migrate.py`
- Modify: `run.sh` (add the `migrate` case)
- Test: `tests/test_migrate.py`

**Interfaces:**
- Consumes: `hls.Store` (old store, read), the two footprint GeoJSON files, `catalog.Catalog`, `refresh.finish_grids`.
- Produces: `migrate.import_hls(cat, store_path, mission_key="hls", now=months.now_iso, out=sys.stdout) -> dict` (`{"granules", "months", "tiles"}`); `migrate.import_footprints(cat, path, mission, now, out) -> dict` (`{"granules", "months"}`) for a swath mission; `migrate.main(argv=None) -> int` with `migrate [--registry PATH] [--catalog PATH] [--hls PATH] [--emit PATH] [--eco PATH]`, defaults `paths.HLS_DB`, `paths.EMIT_FOOTPRINTS`, `paths.ECO_FOOTPRINTS`; a missing source is reported and skipped.

Mapping rules: HLS `acq` rows → `granules` with `mission="hls"`, `id`, `start=time`, `end=time`, `tile`, `sensor`, `cloud` (as REAL); `months` rows keyed by sensor collapse to one `(hls, month)` with the summed count and the latest `fetched_at`; `tiles` rows → `tiles(grid="mgrs")`. EMIT features → `granules` with `mission="emit"`, `id`, `start`, `end`, `cloud`, `browse`, `data`, `ring` from the polygon, box from the ring; the baked `eco` and `hls_near` properties are dropped. ECOSTRESS features → `mission="eco"`, `id`, `start`, `end`, `daynight`, `orbit`, ring and box. Swath months: one `months` row per month present among the imported rows, `count` = rows in that month, `fetched_at = now()`. After the imports, `refresh.finish_grids` computes coverage (tiles already imported).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migrate.py`:

```python
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import catalog, hls, migrate, paths, registry

NOW = lambda: "2025-09-30T00:00:00+00:00"
RING_IA = [[-94, 41.5], [-93, 41.5], [-93, 42.5], [-94, 42.5], [-94, 41.5]]


def write_geojson(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


class TestMigrate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cat = catalog.Catalog(self.tmp / "c.sqlite")
        self.out = io.StringIO()
        store = hls.Store(self.tmp / "hls.sqlite")
        store.replace_month("S30", "2025-07", [
            {"id": "HLS.S30.T15TVH.2025199T170000.v2.0", "tile": "T15TVH", "date": "2025-07-18", "time": "2025-07-18T17:00:00Z", "sensor": "S30", "cloud": 10},
            {"id": "HLS.S30.T15TVH.2025200T170000.v2.0", "tile": "T15TVH", "date": "2025-07-19", "time": "2025-07-19T17:00:00Z", "sensor": "S30", "cloud": None},
        ], "2025-08-01T00:00:00+00:00")
        store.replace_month("L30", "2025-07", [
            {"id": "HLS.L30.T15TVH.2025198T170000.v2.0", "tile": "T15TVH", "date": "2025-07-17", "time": "2025-07-17T17:00:00Z", "sensor": "L30", "cloud": 50},
        ], "2025-08-02T00:00:00+00:00")
        store.put_tiles([("T15TVH", hls.tile_ring("T15TVH"))])
        store.close()
        write_geojson(self.tmp / "emit.geojson", [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [RING_IA]},
            "properties": {"id": "EMIT_1", "start": "2025-07-18T16:00:00.000Z", "end": "2025-07-18T16:00:12.000Z", "cloud": 12.0,
                           "year": 2025, "browse": "https://x/EMIT_1.png", "data": "https://x/EMIT_1.nc",
                           "eco": [{"id": "ECO_1", "dt": 180}], "hls_near": [{"date": "2025-07-18"}]}}])
        write_geojson(self.tmp / "eco.geojson", [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[-95, 41], [-92, 41], [-92, 43], [-95, 43], [-95, 41]]]},
            "properties": {"id": "ECO_1", "start": "2025-07-18T16:03:00.000Z", "end": "2025-07-18T16:03:52.000Z",
                           "daynight": "DAY", "year": 2025, "orbit": 39607}}])
        self.reg = registry.load()

    def tearDown(self):
        self.cat.close()
        shutil.rmtree(self.tmp)

    def test_import_hls_collapses_sensor_months_and_copies_tiles(self):
        counts = migrate.import_hls(self.cat, self.tmp / "hls.sqlite", now=NOW, out=self.out)
        self.assertEqual(counts, {"granules": 3, "months": 1, "tiles": 1})
        rows = self.cat.conn.execute("SELECT id, tile, sensor, cloud, start FROM granules WHERE mission='hls' ORDER BY start").fetchall()
        self.assertEqual([(r["sensor"], r["cloud"]) for r in rows], [("L30", 50.0), ("S30", 10.0), ("S30", None)])
        self.assertEqual(rows[0]["start"], "2025-07-17T17:00:00Z")
        month = self.cat.conn.execute("SELECT count, fetched_at FROM months WHERE mission='hls'").fetchone()
        self.assertEqual((month["count"], month["fetched_at"]), (3, "2025-08-02T00:00:00+00:00"))
        self.assertEqual(self.cat.known_tiles("mgrs"), {"T15TVH"})

    def test_import_footprints_drops_baked_pairing_and_records_months(self):
        counts = migrate.import_footprints(self.cat, self.tmp / "emit.geojson", self.reg.mission("emit"), now=NOW, out=self.out)
        self.assertEqual(counts, {"granules": 1, "months": 1})
        row = self.cat.conn.execute("SELECT * FROM granules WHERE mission='emit'").fetchone()
        self.assertEqual((row["cloud"], row["browse"], row["data"], row["attrs"]), (12.0, "https://x/EMIT_1.png", "https://x/EMIT_1.nc", None))
        self.assertEqual((row["minlon"], row["maxlat"]), (-94.0, 42.5))
        self.assertEqual(self.cat.fetched_at("emit", "2025-07"), NOW())
        counts = migrate.import_footprints(self.cat, self.tmp / "eco.geojson", self.reg.mission("eco"), now=NOW, out=self.out)
        row = self.cat.conn.execute("SELECT daynight, orbit FROM granules WHERE mission='eco'").fetchone()
        self.assertEqual((row["daynight"], row["orbit"]), ("DAY", 39607))

    def test_migrate_is_idempotent_and_computes_coverage(self):
        args = ["--registry", str(paths.MISSIONS), "--catalog", str(self.tmp / "m.sqlite"),
                "--hls", str(self.tmp / "hls.sqlite"), "--emit", str(self.tmp / "emit.geojson"), "--eco", str(self.tmp / "eco.geojson")]
        with unittest.mock.patch("sys.stdout", self.out):
            self.assertEqual(migrate.main(args), 0)
            self.assertEqual(migrate.main(args), 0)
        cat = catalog.open_read_only(self.tmp / "m.sqlite")
        try:
            self.assertEqual(cat.conn.execute("SELECT COUNT(*) FROM granules").fetchone()[0], 5)
            self.assertEqual(cat.conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0], 1)
            self.assertEqual(cat.conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0], 2)     # EMIT and ECO both touch T15TVH
            self.assertEqual(cat.summary("hls")["count"], 3)
        finally:
            cat.close()

    def test_missing_sources_are_reported_and_skipped(self):
        with unittest.mock.patch("sys.stdout", self.out), unittest.mock.patch("sys.stderr", io.StringIO()) as err:
            code = migrate.main(["--catalog", str(self.tmp / "m.sqlite"), "--hls", str(self.tmp / "none.sqlite"),
                                 "--emit", str(self.tmp / "none.geojson"), "--eco", str(self.tmp / "none2.geojson")])
        self.assertEqual(code, 0)
        self.assertIn("none.sqlite", err.getvalue())


import unittest.mock  # noqa: E402
```

Put `import unittest.mock` with the other imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_migrate -v`
Expected: FAIL with `ImportError: cannot import name 'migrate'`.

- [ ] **Step 3: Write the command**

Create `viz/migrate.py`:

```python
"""Import the pre-registry stores into the region catalog, once.

Run by ./run.sh migrate. The HLS store (data/hls/hls.sqlite) and the EMIT
and ECOSTRESS footprint files are copied into data/catalog/<region>.sqlite so
nothing is fetched again; the baked pairing properties are dropped because
coincidence becomes a query. Safe to run more than once.
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from viz import catalog, months, paths, refresh, registry, spatial


def _months_of(rows):
    by_month = defaultdict(list)
    for row in rows:
        by_month[row["start"][:7]].append(row)
    return by_month


def import_hls(cat, store_path, mission_key="hls", now=months.now_iso, out=sys.stdout):
    """Copy acq rows, collapsed sensor months, and tile rings from the old HLS store."""
    conn = sqlite3.connect(f"file:{Path(store_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [{"id": r["id"], "start": r["time"], "end": r["time"], "tile": r["tile"], "sensor": r["sensor"],
                 "cloud": float(r["cloud"]) if r["cloud"] is not None else None}
                for r in conn.execute("SELECT id, tile, time, sensor, cloud FROM acq")]
        fetched = {}
        for r in conn.execute("SELECT month, MAX(fetched_at) AS f FROM months GROUP BY month"):
            fetched[r["month"]] = r["f"]
        tiles = [(r["tile"], json.loads(r["ring"])) for r in conn.execute("SELECT tile, ring FROM tiles")]
    finally:
        conn.close()
    by_month = _months_of(rows)
    for month, month_rows in sorted(by_month.items()):
        cat.replace_month(mission_key, month, month_rows, fetched.get(month) or now())
    cat.put_tiles("mgrs", tiles)
    print(f"{mission_key}: {len(rows)} granules over {len(by_month)} months, {len(tiles)} tiles imported", file=out)
    return {"granules": len(rows), "months": len(by_month), "tiles": len(tiles)}


_DROPPED = {"eco", "hls_near", "year"}


def import_footprints(cat, path, mission, now=months.now_iso, out=sys.stdout):
    """Copy a swath mission's footprint file into the catalog, dropping the baked pairing."""
    data = json.loads(Path(path).read_text())
    rows = []
    for feature in data.get("features", []):
        props = feature["properties"]
        ring = feature["geometry"]["coordinates"][0]
        minlon, minlat, maxlon, maxlat = spatial.ring_bbox(ring)
        row = {"id": props["id"], "start": props["start"], "end": props.get("end") or props["start"],
               "minlon": minlon, "minlat": minlat, "maxlon": maxlon, "maxlat": maxlat, "ring": ring,
               "browse": props.get("browse"), "data": props.get("data")}
        for name in mission.attributes:
            row[name] = props.get(name)
        rows.append(row)
    by_month = _months_of(rows)
    for month, month_rows in sorted(by_month.items()):
        cat.replace_month(mission.key, month, month_rows, now())
    print(f"{mission.key}: {len(rows)} granules over {len(by_month)} months imported", file=out)
    return {"granules": len(rows), "months": len(by_month)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import the old HLS store and footprint files into the catalog.")
    parser.add_argument("--registry", default=str(paths.MISSIONS))
    parser.add_argument("--catalog")
    parser.add_argument("--hls", default=str(paths.HLS_DB))
    parser.add_argument("--emit", default=str(paths.EMIT_FOOTPRINTS))
    parser.add_argument("--eco", default=str(paths.ECO_FOOTPRINTS))
    args = parser.parse_args(argv)

    reg = registry.load(args.registry)
    cat = catalog.Catalog(args.catalog or paths.catalog_db(reg.region.name))
    try:
        if Path(args.hls).is_file():
            import_hls(cat, args.hls)
        else:
            print(f"migrate: no HLS store at {args.hls}; skipped", file=sys.stderr)
        for key, source in (("emit", args.emit), ("eco", args.eco)):
            if key in reg.missions and Path(source).is_file():
                import_footprints(cat, source, reg.mission(key))
            else:
                print(f"migrate: no {key} footprints at {source}; skipped", file=sys.stderr)
        refresh.finish_grids(cat, reg)
        for key in cat.missions_present():
            summary = cat.summary(key)
            print(f"{key}: {summary['count']} granules over {summary['months']} months in {cat.path}")
    finally:
        cat.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `run.sh`, add before the `serve)` case:

```bash
  migrate)
    # One-time import of data/hls/hls.sqlite and the EMIT and ECOSTRESS footprint
    # files into the region catalog, so nothing is fetched again. Safe to rerun.
    shift
    exec python3 -m viz.migrate "$@"
    ;;
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_migrate -v`
Expected: PASS, 4 tests. Coverage in the idempotence test: the EMIT ring (Iowa square) and the ECOSTRESS box both intersect tile T15TVH's ring, giving two coverage rows; a second run writes none (`INSERT OR IGNORE` and the `uncovered` query).

- [ ] **Step 5: Run the real migration once and record it**

Run: `PYTHONNOUSERSITE=1 ./run.sh migrate 2>&1 | tail -6`
Expected: lines like `hls: 1269044 granules over 45 months, 1223 tiles imported`, `emit: 28882 granules …`, `eco: 75730 granules …`, `mgrs: 0 tile outlines computed`, `emit coverage: N rows against mgrs`, `eco coverage: M rows against mgrs`, and the file `data/catalog/conus.sqlite` present. Record the printed counts, the run time, and the file size in the report; expect the coverage pass to take under two minutes. Then `ls -la data/catalog/`.

- [ ] **Step 6: Run the full suite**

Run: `PYTHONNOUSERSITE=1 ./run.sh test 2>&1 | tail -3`
Expected: OK.

- [ ] **Step 7: Commit point**

Message: `Add the migrate command importing the old stores into the catalog`.

---

### Task 10: Data directory hygiene and the serve note

**Files:**
- Modify: `.gitignore` (confirm `data/` is ignored; add `data/catalog/` explicitly if `data/` is not wholly ignored)
- Modify: `run.sh` (`serve` case: a note when the catalog is absent, beside the existing notes; no behaviour change)
- Test: `tests/test_run_sh.py` (new, static) or an assertion in an existing static test

**Interfaces:** none.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_sh.py`:

```python
import subprocess
import unittest
from pathlib import Path

from viz import paths


class TestRunSh(unittest.TestCase):
    def test_run_sh_has_refresh_and_migrate_cases_and_parses(self):
        text = (paths.ROOT / "run.sh").read_text()
        self.assertIn("  refresh)", text)
        self.assertIn("  migrate)", text)
        self.assertIn("python3 -m viz.refresh", text)
        self.assertIn("python3 -m viz.migrate", text)
        self.assertIn("data/catalog/conus.sqlite", text)
        self.assertIn("refresh|migrate", text)
        self.assertEqual(subprocess.run(["bash", "-n", str(paths.ROOT / "run.sh")]).returncode, 0)

    def test_catalog_directory_is_ignored(self):
        ignore = (paths.ROOT / ".gitignore").read_text().splitlines()
        self.assertTrue(any(line.strip() in ("data/", "data", "/data/", "/data") or line.strip().startswith("data/catalog") for line in ignore))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_run_sh -v`
Expected: FAIL on the `data/catalog/conus.sqlite` assertion (the note is not there yet); the ignore test passes if `data/` is already ignored.

- [ ] **Step 3: Add the note**

In the `serve)` case of `run.sh`, after the HLS note, add:

```bash
    if [ ! -f data/catalog/conus.sqlite ]; then
      echo "note: no region catalog (data/catalog/conus.sqlite); run ./run.sh migrate once, then ./run.sh refresh" >&2
    fi
```

Confirm `.gitignore` covers `data/` (it does today for `data/hls` and `data/emit`); if it lists subdirectories individually, add `data/catalog/`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_run_sh -v` then `PYTHONNOUSERSITE=1 ./run.sh test 2>&1 | tail -3`
Expected: PASS; suite OK.

- [ ] **Step 5: Commit point**

Message: `Note the missing catalog at serve time and wire the new commands`.

---

## Appendix: migration record

Filled in by the executor after Task 9 step 5: the printed counts of `./run.sh migrate` on the real data, the run time, and the catalog file size.
