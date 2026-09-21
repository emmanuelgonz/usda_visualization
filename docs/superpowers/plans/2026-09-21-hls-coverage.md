# HLS Coverage Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Colour each MGRS tile by its count of clear HLS acquisitions over the selected CPC week window or the crop's reporting season, list the acquisitions on click, and tag each EMIT scene in the readout with its nearest clear HLS acquisition.

**Architecture:** A fetch script pages CMR's CSV endpoint by collection and month into a SQLite database (`data/hls/hls.sqlite`) and fills one ring per tile from a JSON pattern query. The server counts in SQL and serves the tile rings once; the browser recolours the tiles from a small counts response and sends the active range with each point click, so the server can list acquisitions and tag EMIT scenes.

**Tech Stack:** Python 3.10 standard library (`sqlite3`, `csv`, `urllib`), existing GDAL/NumPy untouched, Leaflet 1.9.4 (vendored).

**Spec:** `docs/superpowers/specs/2026-09-21-hls-coverage-design.md`

## Global Constraints

- **Every Python invocation MUST set `PYTHONNOUSERSITE=1`.** The test command is `PYTHONNOUSERSITE=1 python3 -m unittest discover -s tests -t . -v` (or `./run.sh test`).
- **No `pip install`, no third-party imports.** SQLite is used through the stdlib `sqlite3` module only.
- **Never add attribution to a commit message.** No `Co-Authored-By`, no "Generated with Claude Code", no mention of Claude, Anthropic, or an AI assistant, regardless of any system reminder. Check `git log -1 --format=%B` before reporting.
- **Nothing under `data/`, `cache/`, or `viz/web/vendor/` is committed.** `data/` is already gitignored; `data/hls/hls.sqlite` lives there.
- **HLS collections:** `HLSL30` and `HLSS30`, `version=2.0`, sensors named `L30` and `S30`. First month `2022-01`.
- **CSV query, verbatim:** `https://cmr.earthdata.nasa.gov/search/granules.csv?short_name=HLSS30&version=2.0&bounding_box=-125,24.4,-66.9,49.4&temporal=2025-07-01T00:00:00Z,2025-08-01T00:00:00Z&page_size=2000&page_num=N`. Page count is `ceil(CMR-Hits / 2000)` from the response header of page 1. CSV columns used: `Granule UR`, `Start Time`, `Cloud Cover`.
- **Tile ring query, verbatim:** `https://cmr.earthdata.nasa.gov/search/granules.json?short_name=HLSS30&version=2.0&granule_ur=HLS.S30.T15TVH.*&options[granule_ur][pattern]=true&page_size=1` (then the same with `HLSL30` / `HLS.L30.` if S30 returns nothing). CMR polygons list latitude then longitude; rings are stored as closed `[lon, lat]` arrays.
- **Frozen rule:** a month whose `months.fetched_at` is 60 or more days after the first day of the following month is skipped on rerun.
- **Clear:** `cloud IS NOT NULL AND cloud <= threshold`, with the sensor filter applied. A blank or non-numeric cloud value is stored as NULL.
- **Defaults:** HLS layer off; Mode "Week window"; Max cloud 30; Sensor Both (`ALL`); shared Time window 7 days. Season = Monday of the first CPC week to Sunday of the last CPC week present in the catalog for the selected crop, variable, and year.
- **`dt` for the EMIT tag** is a signed whole number of calendar days, HLS date minus EMIT date; nearest by `|dt|`, ties to the earlier acquisition time; only within `±window` days.
- **Ramp:** classes 1, 2, 3–4, 5–8, 9+ = `#e7d4e8`, `#c2a5cf`, `#9970ab`, `#762a83`, `#40004b`; fill opacity 0.55; outline `#40004b` weight 0.5; zero-count tiles outline only at opacity 0.25. Style functions return the same key set on every call.
- **Pane order:** cpc 450, **hls 451**, eco 452, emit 455, states 460.
- Server restart after any Python change. A server may run on port 8000 for the user; probe on 8765 and stop it.

## File Structure

| Path | Responsibility |
| --- | --- |
| `viz/paths.py` | `HLS_DATA`, `HLS_DB` |
| `viz/hls.py` | Granule-UR parsing, month arithmetic, frozen rule, CSV parsing, `Store` (schema, month replacement, tiles, counts, acquisitions, summary), `TileIndex`, `store_for`, `is_clear`, `nearest_clear` |
| `viz/cmr.py` | Gains `fetch_response(url)` (bytes + headers, retried) and `polygon_ring(entry)` |
| `viz/fetch_emit.py` | Uses `cmr.polygon_ring`; interface unchanged |
| `viz/fetch_hls.py` | URL builders, `fetch_month`, `fetch_ring`, `main` |
| `viz/tileserver.py` | `/api/hls/tiles.geojson`, `/api/hls/counts`, HLS block and EMIT tags on `/api/point`, three catalog fields |
| `run.sh` | `hls` subcommand; `footprints` runs it last; `serve` note |
| `viz/web/index.html`, `style.css`, `app.js` | Shared "Time window" block, HLS controls, tile layer, counts refresh, legend, readout block, EMIT tags |
| `tests/test_hls.py`, `tests/test_fetch_hls.py`, `tests/test_cmr.py`, `tests/test_server.py` | Tests |

---

### Task 1: Granule parsing, month arithmetic, and CSV parsing

**Files:**
- Modify: `viz/paths.py`
- Create: `viz/hls.py`
- Create: `tests/test_hls.py`

**Interfaces:**
- Produces: `hls.parse_ur(granule_ur) -> (sensor, tile) | None`; `hls.months_between(first, last) -> [str]`; `hls.month_bounds(month) -> (start_iso, next_month_iso)`; `hls.is_frozen(month, fetched_at, days=60) -> bool`; `hls.parse_csv(text) -> [dict]` with keys `id, tile, date, time, sensor, cloud`; constants `hls.SENSORS`, `hls.FIRST_MONTH`, `hls.FROZEN_AFTER_DAYS`; `paths.HLS_DATA`, `paths.HLS_DB`.

- [ ] **Step 1: Add the paths**

Append to `viz/paths.py` after the `ECO_FOOTPRINTS` line:

```python
HLS_DATA = DATA / "hls"
HLS_DB = HLS_DATA / "hls.sqlite"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_hls.py`:

```python
import unittest

from viz import hls

CSV = (
    "Granule UR,Producer Granule ID,Start Time,End Time,Online Access URLs,Browse URLs,Cloud Cover,Day/Night,Size\n"
    "HLS.S30.T19UEQ.2025191T154001.v2.0,HLS.S30.T19UEQ.2025191T154001,2025-07-10T15:49:45.668Z,"
    "2025-07-10T15:49:45.668Z,\"https://a/x.tif,https://a/y.tif\",https://a/x.jpg,14,DAY,1.2\n"
    "HLS.L30.T18STE.2022001T154118.v2.0,HLS.L30.T18STE.2022001T154118,2022-01-01T15:41:18.815Z,"
    "2022-01-01T15:41:18.815Z,https://a/z.tif,,,DAY,1.1\n"
    "HLS.S30.T15TVH.2025203T170849.v2.0,HLS.S30.T15TVH.2025203T170849,2025-07-22T17:08:49.000Z,"
    "2025-07-22T17:08:49.000Z,https://a/w.tif,,100,DAY,1.0\n"
)


class TestParseUr(unittest.TestCase):
    def test_sensor_and_tile(self):
        self.assertEqual(hls.parse_ur("HLS.S30.T15TVH.2025203T170849.v2.0"), ("S30", "T15TVH"))
        self.assertEqual(hls.parse_ur("HLS.L30.T01ABC.2022001T154118.v2.0"), ("L30", "T01ABC"))

    def test_malformed_is_none(self):
        self.assertIsNone(hls.parse_ur("ECOv002_L2_LSTE_39898"))
        self.assertIsNone(hls.parse_ur(""))
        self.assertIsNone(hls.parse_ur(None))


class TestMonths(unittest.TestCase):
    def test_months_between_inclusive_across_a_year_end(self):
        self.assertEqual(hls.months_between("2022-11", "2023-02"),
                         ["2022-11", "2022-12", "2023-01", "2023-02"])

    def test_single_month(self):
        self.assertEqual(hls.months_between("2025-07", "2025-07"), ["2025-07"])

    def test_month_bounds(self):
        self.assertEqual(hls.month_bounds("2025-07"), ("2025-07-01", "2025-08-01"))
        self.assertEqual(hls.month_bounds("2024-12"), ("2024-12-01", "2025-01-01"))

    def test_frozen_at_sixty_days_after_month_end(self):
        # 2025-07 ends at 2025-08-01; 59 days later is 2025-09-29, 60 is 09-30, 61 is 10-01.
        self.assertFalse(hls.is_frozen("2025-07", "2025-09-29T12:00:00+00:00"))
        self.assertTrue(hls.is_frozen("2025-07", "2025-09-30T00:00:00+00:00"))
        self.assertTrue(hls.is_frozen("2025-07", "2025-10-01T00:00:00+00:00"))

    def test_frozen_accepts_z_suffix(self):
        self.assertTrue(hls.is_frozen("2025-07", "2025-12-01T00:00:00Z"))


class TestParseCsv(unittest.TestCase):
    def test_rows(self):
        rows = hls.parse_csv(CSV)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {
            "id": "HLS.S30.T19UEQ.2025191T154001.v2.0", "tile": "T19UEQ", "date": "2025-07-10",
            "time": "2025-07-10T15:49:45.668Z", "sensor": "S30", "cloud": 14,
        })
        self.assertEqual(rows[1]["sensor"], "L30")
        self.assertIsNone(rows[1]["cloud"])          # blank cloud cover
        self.assertEqual(rows[2]["cloud"], 100)

    def test_skips_rows_without_a_parseable_ur_or_start(self):
        text = CSV.splitlines()[0] + "\nNOT.AN.HLS.UR,x,2025-07-10T00:00:00Z,,,,5,DAY,1\n" \
               "HLS.S30.T19UEQ.2025191T154001.v2.0,x,,,,,5,DAY,1\n"
        self.assertEqual(hls.parse_csv(text), [])

    def test_decimal_cloud_is_truncated_to_int(self):
        text = CSV.splitlines()[0] + "\nHLS.S30.T19UEQ.2025191T154001.v2.0,x,2025-07-10T00:00:00Z,,,,14.6,DAY,1\n"
        self.assertEqual(hls.parse_csv(text)[0]["cloud"], 14)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_hls -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'viz.hls'`.

- [ ] **Step 4: Write the module**

Create `viz/hls.py`:

```python
"""HLS acquisition store: granule parsing, month arithmetic, and the SQLite tables.

Data lives in data/hls/hls.sqlite (stdlib sqlite3). viz/fetch_hls.py writes
through Store.replace_month and Store.put_tile; the server reads through a
per-thread read-only Store from store_for(). Months are the unit of fetching
because CMR caps paging depth at one million rows per query.
"""

import csv
import datetime
import io
import re

SENSORS = ("L30", "S30")
FIRST_MONTH = "2022-01"
FROZEN_AFTER_DAYS = 60

_UR_RE = re.compile(r"^HLS\.(L30|S30)\.(T[0-9]{2}[A-Z]{3})\.")


def parse_ur(granule_ur):
    """('S30', 'T15TVH') from 'HLS.S30.T15TVH.2025203T170849.v2.0'; None if malformed."""
    match = _UR_RE.match(granule_ur or "")
    return (match.group(1), match.group(2)) if match else None


def months_between(first, last):
    """Every 'YYYY-MM' from first to last inclusive."""
    year, month = (int(v) for v in first.split("-"))
    last_year, last_month = (int(v) for v in last.split("-"))
    out = []
    while (year, month) <= (last_year, last_month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def month_bounds(month):
    """(first day of the month, first day of the next month) as ISO dates."""
    year, mon = (int(v) for v in month.split("-"))
    start = datetime.date(year, mon, 1)
    end = datetime.date(year + 1, 1, 1) if mon == 12 else datetime.date(year, mon + 1, 1)
    return start.isoformat(), end.isoformat()


def is_frozen(month, fetched_at, days=FROZEN_AFTER_DAYS):
    """True when the month was fetched at least `days` after it ended."""
    _, end = month_bounds(month)
    end_date = datetime.date.fromisoformat(end)
    fetched = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).date()
    return (fetched - end_date).days >= days


def parse_csv(text):
    """Rows {id, tile, date, time, sensor, cloud} from a CMR granules.csv body.

    Rows whose granule UR is not an HLS UR, or whose start time is blank, are
    dropped. A blank or non-numeric cloud cover becomes None.
    """
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        ur = record.get("Granule UR") or ""
        parsed = parse_ur(ur)
        start = record.get("Start Time") or ""
        if parsed is None or len(start) < 10:
            continue
        sensor, tile = parsed
        try:
            cloud = int(float(record.get("Cloud Cover") or ""))
        except ValueError:
            cloud = None
        rows.append({"id": ur, "tile": tile, "date": start[:10], "time": start,
                     "sensor": sensor, "cloud": cloud})
    return rows
```

- [ ] **Step 5: Run to verify pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_hls -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add viz/paths.py viz/hls.py tests/test_hls.py
git commit -m "Parse HLS granule IDs, months, and CMR CSV pages"
```

---

### Task 2: The SQLite store, tile index, and clear-acquisition helpers

**Files:**
- Modify: `viz/hls.py`
- Modify: `tests/test_hls.py`

**Interfaces:**
- Consumes: Task 1's helpers; `viz.emit.point_in_ring(lon, lat, ring)`.
- Produces: `hls.Store(path, read_only=False)` with `.conn`, `.close()`, `.replace_month(sensor, month, rows, fetched_at)`, `.fetched_at(sensor, month) -> str | None`, `.missing_tiles() -> [str]`, `.put_tile(tile, ring)`, `.counts(start, end, cloud, sensor="ALL") -> {tile: int}`, `.acquisitions(tiles, start, end) -> [dict]` (keys `tile, date, time, sensor, cloud`, ordered by time), `.tiles_geojson() -> dict`, `.summary() -> {"count", "fetched", "tiles"}`; `hls.TileIndex(store)` with `.covering(lon, lat) -> [tile]` sorted; `hls.store_for(path) -> (Store, TileIndex) | None`; `hls.is_clear(row, cloud, sensor) -> bool`; `hls.nearest_clear(rows, emit_date, window) -> dict | None` with keys `date, sensor, cloud, dt`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hls.py` (before the `if __name__` block), and add `import shutil, tempfile` and `from pathlib import Path` at the top:

```python
def _row(ur, start, cloud):
    sensor, tile = hls.parse_ur(ur)
    return {"id": ur, "tile": tile, "date": start[:10], "time": start, "sensor": sensor, "cloud": cloud}


SQUARE = [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 43.0], [-94.0, 42.0]]


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = hls.Store(self.tmp / "sub" / "hls.sqlite")
        self.addCleanup(self.store.close)
        self.rows = [
            _row("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10),
            _row("HLS.L30.T15TVH.2025202T160000.v2.0", "2025-07-21T16:00:00Z", 80),
            _row("HLS.S30.T15TVH.2025204T170000.v2.0", "2025-07-23T17:00:00Z", 10),
            _row("HLS.S30.T15TVH.2025206T170000.v2.0", "2025-07-25T17:00:00Z", None),
            _row("HLS.S30.T15TWH.2025199T170000.v2.0", "2025-07-18T17:01:00Z", 0),
        ]
        self.store.replace_month("S30", "2025-07", [r for r in self.rows if r["sensor"] == "S30"],
                                 "2025-08-02T00:00:00+00:00")
        self.store.replace_month("L30", "2025-07", [r for r in self.rows if r["sensor"] == "L30"],
                                 "2025-08-02T00:00:00+00:00")


class TestStoreWrites(StoreTestCase):
    def test_schema_creates_parent_directory_and_tables(self):
        names = {r[0] for r in self.store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"acq", "tiles", "months"})

    def test_replace_month_is_idempotent(self):
        self.store.replace_month("S30", "2025-07", [r for r in self.rows if r["sensor"] == "S30"],
                                 "2025-08-03T00:00:00+00:00")
        n = self.store.conn.execute("SELECT COUNT(*) FROM acq").fetchone()[0]
        self.assertEqual(n, 5)
        self.assertEqual(self.store.fetched_at("S30", "2025-07"), "2025-08-03T00:00:00+00:00")
        self.assertIsNone(self.store.fetched_at("S30", "2025-06"))

    def test_replace_month_drops_rows_no_longer_present(self):
        self.store.replace_month("S30", "2025-07", [self.rows[0]], "2025-08-03T00:00:00+00:00")
        n = self.store.conn.execute("SELECT COUNT(*) FROM acq WHERE sensor='S30'").fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(self.store.conn.execute(
            "SELECT count FROM months WHERE sensor='S30' AND month='2025-07'").fetchone()[0], 1)

    def test_missing_tiles_and_put_tile(self):
        self.assertEqual(self.store.missing_tiles(), ["T15TVH", "T15TWH"])
        self.store.put_tile("T15TVH", SQUARE)
        self.assertEqual(self.store.missing_tiles(), ["T15TWH"])
        geo = self.store.tiles_geojson()
        self.assertEqual(geo["type"], "FeatureCollection")
        self.assertEqual(geo["features"][0]["properties"], {"tile": "T15TVH"})
        self.assertEqual(geo["features"][0]["geometry"]["coordinates"][0], SQUARE)

    def test_summary(self):
        self.store.put_tile("T15TVH", SQUARE)
        self.assertEqual(self.store.summary(),
                         {"count": 5, "fetched": "2025-08-02T00:00:00+00:00", "tiles": 1})


class TestStoreReads(StoreTestCase):
    def test_counts_apply_cloud_and_sensor(self):
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 30), {"T15TVH": 2, "T15TWH": 1})
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 30, "L30"), {})
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 80, "L30"), {"T15TVH": 1})
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 10, "S30"), {"T15TVH": 2, "T15TWH": 1})

    def test_counts_cloud_boundary_and_null(self):
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 9), {"T15TWH": 1})
        self.assertEqual(self.store.counts("2025-07-01", "2025-07-31", 100), {"T15TVH": 3, "T15TWH": 1})

    def test_counts_respect_the_date_range_inclusively(self):
        self.assertEqual(self.store.counts("2025-07-18", "2025-07-18", 30), {"T15TVH": 1, "T15TWH": 1})
        self.assertEqual(self.store.counts("2025-07-19", "2025-07-22", 100), {"T15TVH": 1})

    def test_acquisitions_are_ordered_by_time_and_carry_nulls(self):
        rows = self.store.acquisitions(["T15TVH"], "2025-07-01", "2025-07-31")
        self.assertEqual([r["date"] for r in rows], ["2025-07-18", "2025-07-21", "2025-07-23", "2025-07-25"])
        self.assertEqual(rows[0], {"tile": "T15TVH", "date": "2025-07-18", "time": "2025-07-18T17:00:00Z",
                                   "sensor": "S30", "cloud": 10})
        self.assertIsNone(rows[3]["cloud"])
        self.assertEqual(self.store.acquisitions([], "2025-07-01", "2025-07-31"), [])

    def test_read_only_store_sees_committed_rows(self):
        ro = hls.Store(self.store.path, read_only=True)
        self.addCleanup(ro.close)
        self.assertEqual(ro.counts("2025-07-01", "2025-07-31", 100)["T15TVH"], 3)
        with self.assertRaises(Exception):
            ro.put_tile("T15TVH", SQUARE)


class TestTileIndex(StoreTestCase):
    def test_covering_is_sorted_and_respects_rings(self):
        self.store.put_tile("T15TWH", [[-93.5, 42.0], [-92.5, 42.0], [-92.5, 43.0], [-93.5, 43.0], [-93.5, 42.0]])
        self.store.put_tile("T15TVH", SQUARE)
        index = hls.TileIndex(self.store)
        self.assertEqual(index.covering(-93.2, 42.5), ["T15TVH", "T15TWH"])   # the overlap strip
        self.assertEqual(index.covering(-93.9, 42.5), ["T15TVH"])
        self.assertEqual(index.covering(-90.0, 42.5), [])

    def test_store_for_returns_none_when_absent_and_reopens_on_change(self):
        self.assertIsNone(hls.store_for(self.tmp / "nope.sqlite"))
        first = hls.store_for(self.store.path)
        self.assertIsInstance(first[0], hls.Store)
        self.assertIsInstance(first[1], hls.TileIndex)
        self.assertIs(hls.store_for(self.store.path)[0], first[0])
        self.store.put_tile("T15TVH", SQUARE)
        import os, time
        os.utime(self.store.path, ns=(time.time_ns() + 10 ** 9, time.time_ns() + 10 ** 9))
        second = hls.store_for(self.store.path)
        self.assertIsNot(second[0], first[0])
        self.assertEqual(second[1].covering(-93.5, 42.5), ["T15TVH"])


class TestClearHelpers(unittest.TestCase):
    ROWS = [
        {"tile": "T1", "date": "2025-07-18", "time": "2025-07-18T17:00:00Z", "sensor": "S30", "cloud": 10},
        {"tile": "T1", "date": "2025-07-21", "time": "2025-07-21T16:00:00Z", "sensor": "L30", "cloud": 80},
        {"tile": "T1", "date": "2025-07-22", "time": "2025-07-22T17:00:00Z", "sensor": "S30", "cloud": 5},
        {"tile": "T1", "date": "2025-07-22", "time": "2025-07-22T16:00:00Z", "sensor": "L30", "cloud": 5},
        {"tile": "T1", "date": "2025-07-25", "time": "2025-07-25T17:00:00Z", "sensor": "S30", "cloud": None},
    ]

    def test_is_clear(self):
        self.assertTrue(hls.is_clear(self.ROWS[0], 30, "ALL"))
        self.assertTrue(hls.is_clear(self.ROWS[0], 10, "S30"))
        self.assertFalse(hls.is_clear(self.ROWS[0], 9, "ALL"))
        self.assertFalse(hls.is_clear(self.ROWS[0], 30, "L30"))
        self.assertFalse(hls.is_clear(self.ROWS[4], 100, "ALL"))

    def test_nearest_clear_picks_smallest_abs_dt_then_earlier_time(self):
        clear = [r for r in self.ROWS if hls.is_clear(r, 30, "ALL")]
        self.assertEqual(hls.nearest_clear(clear, "2025-07-20", 7),
                         {"date": "2025-07-18", "sensor": "S30", "cloud": 10, "dt": -2})
        # 07-22 has two clear rows two days away; the 16:00 L30 row is earlier.
        self.assertEqual(hls.nearest_clear(clear, "2025-07-24", 7)["sensor"], "L30")
        self.assertEqual(hls.nearest_clear(clear, "2025-07-24", 7)["dt"], -2)

    def test_nearest_clear_respects_the_window(self):
        clear = [r for r in self.ROWS if hls.is_clear(r, 30, "ALL")]
        self.assertIsNone(hls.nearest_clear(clear, "2025-08-10", 7))
        self.assertIsNotNone(hls.nearest_clear(clear, "2025-07-29", 7))
        self.assertIsNone(hls.nearest_clear(clear, "2025-07-29", 6))
        self.assertIsNone(hls.nearest_clear([], "2025-07-20", 7))
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_hls -v`
Expected: FAIL with `AttributeError: module 'viz.hls' has no attribute 'Store'`.

- [ ] **Step 3: Implement**

Add to the imports at the top of `viz/hls.py`:

```python
import json
import os
import sqlite3
import threading
from pathlib import Path

from viz.emit import point_in_ring
```

Append to `viz/hls.py`:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS acq (
  id TEXT PRIMARY KEY,
  tile TEXT NOT NULL,
  date TEXT NOT NULL,
  time TEXT NOT NULL,
  sensor TEXT NOT NULL,
  cloud INTEGER
);
CREATE INDEX IF NOT EXISTS acq_date ON acq(date);
CREATE INDEX IF NOT EXISTS acq_tile_date ON acq(tile, date);
CREATE TABLE IF NOT EXISTS tiles (
  tile TEXT PRIMARY KEY,
  ring TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS months (
  sensor TEXT NOT NULL,
  month TEXT NOT NULL,
  count INTEGER NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (sensor, month)
);
"""


class Store:
    """One connection to the HLS database.

    Writable by default (creates the file and schema); read_only for the
    server, which must never create an empty database by accident.
    """

    def __init__(self, path, read_only=False):
        self.path = Path(path)
        if read_only:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path))
            self.conn.executescript(SCHEMA)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    # --- writes (fetch script) ---

    def replace_month(self, sensor, month, rows, fetched_at):
        """Replace one sensor-month in a single transaction and record the fetch."""
        start, end = month_bounds(month)
        with self.conn:
            self.conn.execute("DELETE FROM acq WHERE sensor = ? AND date >= ? AND date < ?",
                              (sensor, start, end))
            self.conn.executemany(
                "INSERT OR REPLACE INTO acq (id, tile, date, time, sensor, cloud) VALUES (?, ?, ?, ?, ?, ?)",
                [(r["id"], r["tile"], r["date"], r["time"], r["sensor"], r["cloud"]) for r in rows])
            self.conn.execute(
                "INSERT OR REPLACE INTO months (sensor, month, count, fetched_at) VALUES (?, ?, ?, ?)",
                (sensor, month, len(rows), fetched_at))

    def fetched_at(self, sensor, month):
        row = self.conn.execute("SELECT fetched_at FROM months WHERE sensor = ? AND month = ?",
                                (sensor, month)).fetchone()
        return row["fetched_at"] if row else None

    def missing_tiles(self):
        """Tiles present in acq but without a ring, sorted."""
        return [r["tile"] for r in self.conn.execute(
            "SELECT DISTINCT tile FROM acq WHERE tile NOT IN (SELECT tile FROM tiles) ORDER BY tile")]

    def put_tile(self, tile, ring):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO tiles (tile, ring) VALUES (?, ?)",
                              (tile, json.dumps(ring)))

    # --- reads (server) ---

    def counts(self, start, end, cloud, sensor="ALL"):
        """Clear acquisitions per tile over an inclusive date range. NULL cloud never counts."""
        sql = "SELECT tile, COUNT(*) AS n FROM acq WHERE date BETWEEN ? AND ? AND cloud <= ?"
        args = [start, end, cloud]
        if sensor != "ALL":
            sql += " AND sensor = ?"
            args.append(sensor)
        sql += " GROUP BY tile"
        return {r["tile"]: r["n"] for r in self.conn.execute(sql, args)}

    def acquisitions(self, tiles, start, end):
        """Every acquisition of the given tiles in the range, oldest first."""
        tiles = list(tiles)
        if not tiles:
            return []
        marks = ",".join("?" * len(tiles))
        sql = (f"SELECT tile, date, time, sensor, cloud FROM acq WHERE tile IN ({marks}) "
               "AND date BETWEEN ? AND ? ORDER BY time, tile")
        return [dict(r) for r in self.conn.execute(sql, [*tiles, start, end])]

    def tiles_geojson(self):
        features = [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [json.loads(r["ring"])]},
            "properties": {"tile": r["tile"]},
        } for r in self.conn.execute("SELECT tile, ring FROM tiles ORDER BY tile")]
        return {"type": "FeatureCollection", "features": features}

    def summary(self):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(count), 0) AS n, MAX(fetched_at) AS f FROM months").fetchone()
        tiles = self.conn.execute("SELECT COUNT(*) AS n FROM tiles").fetchone()["n"]
        return {"count": row["n"], "fetched": row["f"], "tiles": tiles}


class TileIndex:
    """Point-in-tile over the tiles table. MGRS tiles overlap, so a point can hit several."""

    def __init__(self, store):
        self._items = []
        for r in store.conn.execute("SELECT tile, ring FROM tiles"):
            ring = json.loads(r["ring"])
            lons = [p[0] for p in ring]
            lats = [p[1] for p in ring]
            self._items.append(((min(lons), min(lats), max(lons), max(lats)), ring, r["tile"]))

    def covering(self, lon, lat):
        return sorted(
            tile for (minx, miny, maxx, maxy), ring, tile in self._items
            if minx <= lon <= maxx and miny <= lat <= maxy and point_in_ring(lon, lat, ring)
        )


_local = threading.local()


def store_for(path):
    """(Store, TileIndex) for this thread, read-only, reopened when the file changes; None if absent."""
    path = Path(path)
    if not path.is_file():
        return None
    key = (str(path), os.stat(path).st_mtime_ns)
    cached = getattr(_local, "entry", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    if cached is not None:
        cached[1][0].close()
    store = Store(path, read_only=True)
    entry = (store, TileIndex(store))
    _local.entry = (key, entry)
    return entry


def is_clear(row, cloud, sensor="ALL"):
    """Clear means a known cloud value at or under the threshold, from an accepted sensor."""
    if row["cloud"] is None or row["cloud"] > cloud:
        return False
    return sensor == "ALL" or row["sensor"] == sensor


def nearest_clear(rows, emit_date, window):
    """The row nearest emit_date within ±window days, ties to the earlier time; None if none.

    rows must already be filtered with is_clear. dt is HLS date minus EMIT date
    in whole calendar days.
    """
    base = datetime.date.fromisoformat(emit_date)
    best = None
    for row in rows:
        dt = (datetime.date.fromisoformat(row["date"]) - base).days
        if abs(dt) > window:
            continue
        key = (abs(dt), row["time"])
        if best is None or key < best[0]:
            best = (key, {"date": row["date"], "sensor": row["sensor"], "cloud": row["cloud"], "dt": dt})
    return best[1] if best else None
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_hls -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add viz/hls.py tests/test_hls.py
git commit -m "Add the HLS SQLite store, tile index, and clear-acquisition helpers"
```

---

### Task 3: CMR helpers, the fetch script, and run.sh

**Files:**
- Modify: `viz/cmr.py`
- Modify: `viz/fetch_emit.py:18-29`
- Create: `viz/fetch_hls.py`
- Modify: `run.sh`
- Modify: `tests/test_cmr.py`
- Create: `tests/test_fetch_hls.py`

**Interfaces:**
- Consumes: `hls.Store`, `hls.parse_csv`, `hls.month_bounds`, `hls.months_between`, `hls.is_frozen`, `hls.FIRST_MONTH`, `paths.HLS_DB`, `cmr.fetch_page`, `cmr.BBOX`, `cmr.PAGE_SIZE`, `cmr.CMR_URL`.
- Produces: `cmr.fetch_response(url) -> (bytes, headers)` where `headers` supports `.get("CMR-Hits")`; `cmr.polygon_ring(entry) -> [[lon, lat], ...] | None` (closed); `fetch_hls.csv_url(short_name, month, page_num)`, `fetch_hls.tile_urls(tile) -> [str, str]`, `fetch_hls.fetch_month(short_name, month, fetch_fn=cmr.fetch_response) -> [rows]`, `fetch_hls.fetch_ring(tile, fetch_page_fn=cmr.fetch_page) -> ring | None`, `fetch_hls.main(argv=None)`; `./run.sh hls`.

- [ ] **Step 1: Write the failing CMR tests**

Append to `tests/test_cmr.py` before the `if __name__` block:

```python
class TestPolygonRing(unittest.TestCase):
    def test_swaps_to_lon_lat_and_closes(self):
        entry = {"polygons": [["42.0 -94.0 42.0 -93.0 43.0 -93.0"]]}
        self.assertEqual(cmr.polygon_ring(entry),
                         [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 42.0]])

    def test_already_closed_ring_is_not_doubled(self):
        entry = {"polygons": [["42.0 -94.0 42.0 -93.0 43.0 -93.0 42.0 -94.0"]]}
        self.assertEqual(len(cmr.polygon_ring(entry)), 4)

    def test_missing_polygon_is_none(self):
        self.assertIsNone(cmr.polygon_ring({}))
        self.assertIsNone(cmr.polygon_ring({"polygons": []}))
        self.assertIsNone(cmr.polygon_ring({"polygons": [[]]}))


class TestFetchResponse(unittest.TestCase):
    def test_retries_then_returns_body_and_headers(self):
        import io
        from unittest import mock

        class Response(io.BytesIO):
            headers = {"CMR-Hits": "9675"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        calls = []

        def fake_urlopen(url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("boom")
            return Response(b"a,b\n1,2\n")

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("time.sleep"):
            body, headers = cmr.fetch_response("http://x")
        self.assertEqual(body, b"a,b\n1,2\n")
        self.assertEqual(headers.get("CMR-Hits"), "9675")
        self.assertEqual(len(calls), 2)

    def test_gives_up_after_three_failures(self):
        from unittest import mock

        def fake_urlopen(url, timeout):
            raise OSError("boom")

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("time.sleep"):
            with self.assertRaises(OSError):
                cmr.fetch_response("http://x")
```

- [ ] **Step 2: Write the failing fetch tests**

Create `tests/test_fetch_hls.py`:

```python
import unittest

from viz import cmr, fetch_hls, hls

HEADER = "Granule UR,Producer Granule ID,Start Time,End Time,Online Access URLs,Browse URLs,Cloud Cover,Day/Night,Size\n"


def csv_rows(*specs):
    return HEADER + "".join(f"{ur},x,{start},{start},https://a/x.tif,,{cloud},DAY,1\n" for ur, start, cloud in specs)


class TestUrls(unittest.TestCase):
    def test_csv_url_is_bounded_to_the_month(self):
        url = fetch_hls.csv_url("HLSS30", "2025-07", 3)
        self.assertTrue(url.startswith("https://cmr.earthdata.nasa.gov/search/granules.csv?"))
        self.assertIn("short_name=HLSS30", url)
        self.assertIn("version=2.0", url)
        self.assertIn("bounding_box=" + cmr.BBOX, url)
        self.assertIn("temporal=2025-07-01T00:00:00Z,2025-08-01T00:00:00Z", url)
        self.assertIn("page_size=2000", url)
        self.assertIn("page_num=3", url)

    def test_tile_urls_try_s30_then_l30(self):
        urls = fetch_hls.tile_urls("T15TVH")
        self.assertEqual(len(urls), 2)
        self.assertIn("short_name=HLSS30", urls[0])
        self.assertIn("granule_ur=HLS.S30.T15TVH.*", urls[0])
        self.assertIn("options[granule_ur][pattern]=true", urls[0])
        self.assertIn("page_size=1", urls[0])
        self.assertIn("short_name=HLSL30", urls[1])
        self.assertIn("granule_ur=HLS.L30.T15TVH.*", urls[1])
        self.assertTrue(urls[0].startswith(cmr.CMR_URL + "?"))


class TestFetchMonth(unittest.TestCase):
    def test_pages_by_hits_and_filters_to_the_month(self):
        pages = {
            1: csv_rows(("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10),
                        ("HLS.S30.T15TVH.2025200T170000.v2.0", "2025-07-19T17:00:00Z", 20)),
            2: csv_rows(("HLS.S30.T15TVH.2025201T170000.v2.0", "2025-07-20T17:00:00Z", 30),
                        ("HLS.S30.T15TVH.2025213T000000.v2.0", "2025-08-01T00:00:00Z", 0)),
        }
        asked = []

        def fake(url):
            page = int(url.rsplit("page_num=", 1)[1])
            asked.append(page)
            return pages[page].encode(), {"CMR-Hits": "4"}

        with patch_page_size(2):
            rows = fetch_hls.fetch_month("HLSS30", "2025-07", fetch_fn=fake)
        self.assertEqual(asked, [1, 2])
        self.assertEqual([r["date"] for r in rows], ["2025-07-18", "2025-07-19", "2025-07-20"])

    def test_zero_hits_is_one_request_and_no_rows(self):
        asked = []

        def fake(url):
            asked.append(url)
            return HEADER.encode(), {"CMR-Hits": "0"}

        self.assertEqual(fetch_hls.fetch_month("HLSL30", "2022-01", fetch_fn=fake), [])
        self.assertEqual(len(asked), 1)


class patch_page_size:
    """Temporarily shrink cmr.PAGE_SIZE so a two-page test needs four rows, not 4,000."""

    def __init__(self, size):
        self.size = size

    def __enter__(self):
        self.saved = cmr.PAGE_SIZE
        cmr.PAGE_SIZE = self.size

    def __exit__(self, *args):
        cmr.PAGE_SIZE = self.saved
        return False


class TestFetchRing(unittest.TestCase):
    def test_takes_the_first_polygon_from_the_first_collection_that_has_one(self):
        asked = []

        def fake(url):
            asked.append(url)
            if "HLSS30" in url:
                return []
            return [{"polygons": [["42.0 -94.0 42.0 -93.0 43.0 -93.0"]]}]

        ring = fetch_hls.fetch_ring("T15TVH", fetch_page_fn=fake)
        self.assertEqual(ring, [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 42.0]])
        self.assertEqual(len(asked), 2)

    def test_none_when_neither_collection_has_a_polygon(self):
        self.assertIsNone(fetch_hls.fetch_ring("T15TVH", fetch_page_fn=lambda url: [{"polygons": []}]))


class TestMain(unittest.TestCase):
    def test_skips_frozen_months_and_fills_missing_rings(self):
        import shutil
        import tempfile
        from pathlib import Path
        from unittest import mock

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db = tmp / "hls.sqlite"
        seed = hls.Store(db)
        seed.replace_month("S30", "2025-05", [], "2025-12-01T00:00:00+00:00")   # frozen
        seed.replace_month("L30", "2025-05", [], "2025-12-01T00:00:00+00:00")   # frozen
        seed.close()

        fetched = []

        def fake_month(short_name, month, fetch_fn=None):
            fetched.append((short_name, month))
            if month == "2025-06":
                return hls.parse_csv(csv_rows(("HLS.S30.T15TVH.2025160T170000.v2.0", "2025-06-09T17:00:00Z", 10)))
            return []

        rings = []

        def fake_ring(tile, fetch_page_fn=None):
            rings.append(tile)
            return [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 42.0]]

        with mock.patch.object(fetch_hls, "fetch_month", fake_month), \
             mock.patch.object(fetch_hls, "fetch_ring", fake_ring), \
             mock.patch.object(fetch_hls, "current_month", lambda: "2025-06"), \
             mock.patch.object(fetch_hls.paths, "HLS_DB", db):
            self.assertEqual(fetch_hls.main(["--from", "2025-05"]), 0)

        self.assertEqual(sorted(fetched), [("HLSL30", "2025-06"), ("HLSS30", "2025-06")])
        self.assertEqual(rings, ["T15TVH"])
        store = hls.Store(db, read_only=True)
        self.addCleanup(store.close)
        self.assertEqual(store.summary()["count"], 1)
        self.assertEqual(store.summary()["tiles"], 1)
        self.assertEqual(store.missing_tiles(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_cmr tests.test_fetch_hls -v`
Expected: FAIL with `AttributeError: module 'viz.cmr' has no attribute 'polygon_ring'` and `ModuleNotFoundError: No module named 'viz.fetch_hls'`.

- [ ] **Step 4: Extend `viz/cmr.py`**

Update the module docstring's second line to `Used by the EMIT, ECOSTRESS, and HLS fetch scripts.` Then add after `fetch_page`:

```python
def fetch_response(url):
    """(body bytes, response headers) from one URL. Retries transient failures three times."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read(), response.headers
        except OSError as exc:
            if attempt == 2:
                raise
            print(f"{url[-40:]}: {exc}; retrying", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def polygon_ring(entry):
    """The entry's first polygon as a closed [lon, lat] ring, or None.

    CMR lists latitude then longitude; GeoJSON wants longitude then latitude.
    """
    polygons = entry.get("polygons")
    if not polygons or not polygons[0]:
        return None
    numbers = [float(v) for v in polygons[0][0].split()]
    ring = [[numbers[i + 1], numbers[i]] for i in range(0, len(numbers), 2)]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring
```

- [ ] **Step 5: Use `polygon_ring` in `viz/fetch_emit.py`**

Replace lines 18–27 of `viz/fetch_emit.py` (from `polygons = entry.get("polygons")` through `ring.append(ring[0])`) with:

```python
    ring = cmr.polygon_ring(entry)
    if ring is None:
        return None
```

Keep the rest of `entry_to_feature` unchanged. Run `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_fetch_emit -v` and confirm it still passes.

- [ ] **Step 6: Write `viz/fetch_hls.py`**

```python
"""Fetch HLS (HLSL30 and HLSS30 v2.0) granule metadata over CONUS into SQLite.

Run by ./run.sh hls (and as the last step of ./run.sh footprints). Each
collection-month is one CSV query paged by the CMR-Hits header, because CMR
caps paging depth at one million rows per query and the JSON endpoint is
five times heavier than CSV. Months fetched well after they ended are frozen
and skipped, so a rerun touches only recent months. Tile rings are filled
once per tile from a JSON pattern query.
"""

import argparse
import datetime
import sys

from viz import cmr, hls, paths

SHORT_NAMES = {"L30": "HLSL30", "S30": "HLSS30"}
VERSION = "2.0"
CSV_URL = "https://cmr.earthdata.nasa.gov/search/granules.csv"


def csv_url(short_name, month, page_num):
    start, end = hls.month_bounds(month)
    return (f"{CSV_URL}?short_name={short_name}&version={VERSION}&bounding_box={cmr.BBOX}"
            f"&temporal={start}T00:00:00Z,{end}T00:00:00Z"
            f"&page_size={cmr.PAGE_SIZE}&page_num={page_num}")


def tile_urls(tile):
    """One pattern query per collection, S30 first; either returns the tile's ring."""
    return [
        f"{cmr.CMR_URL}?short_name={SHORT_NAMES[sensor]}&version={VERSION}"
        f"&granule_ur=HLS.{sensor}.{tile}.*&options[granule_ur][pattern]=true&page_size=1"
        for sensor in ("S30", "L30")
    ]


def current_month():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def fetch_month(short_name, month, fetch_fn=cmr.fetch_response):
    """Every row of one collection-month, paged by CMR-Hits, limited to the month's dates."""
    body, headers = fetch_fn(csv_url(short_name, month, 1))
    hits = int(headers.get("CMR-Hits") or 0)
    rows = hls.parse_csv(body.decode("utf-8"))
    pages = -(-hits // cmr.PAGE_SIZE)
    for page in range(2, pages + 1):
        body, _ = fetch_fn(csv_url(short_name, month, page))
        rows.extend(hls.parse_csv(body.decode("utf-8")))
    start, end = hls.month_bounds(month)
    return [r for r in rows if start <= r["date"] < end]


def fetch_ring(tile, fetch_page_fn=cmr.fetch_page):
    """The tile's closed [lon, lat] ring from the first collection that has one, or None."""
    for url in tile_urls(tile):
        for entry in fetch_page_fn(url):
            ring = cmr.polygon_ring(entry)
            if ring:
                return ring
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch HLS granule metadata into SQLite.")
    parser.add_argument("--from", dest="first", default=hls.FIRST_MONTH,
                        help="first month to fetch, YYYY-MM (default 2022-01)")
    args = parser.parse_args(argv)

    store = hls.Store(paths.HLS_DB)
    try:
        months = hls.months_between(args.first, current_month())
        for sensor, short_name in SHORT_NAMES.items():
            for month in months:
                fetched = store.fetched_at(sensor, month)
                if fetched and hls.is_frozen(month, fetched):
                    continue
                rows = fetch_month(short_name, month)
                store.replace_month(sensor, month, rows, now_iso())
                print(f"hls {sensor} {month}: {len(rows)} granules", flush=True)

        missing = store.missing_tiles()
        for i, tile in enumerate(missing, 1):
            ring = fetch_ring(tile)
            if ring is None:
                print(f"hls: no polygon for {tile}; it is counted but not drawn", file=sys.stderr)
                continue
            store.put_tile(tile, ring)
            if i % 50 == 0 or i == len(missing):
                print(f"hls tiles: {i}/{len(missing)}", flush=True)

        summary = store.summary()
        print(f"hls: {summary['count']} granules, {summary['tiles']} tiles in {paths.HLS_DB}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Run to verify pass**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_cmr tests.test_fetch_hls tests.test_fetch_emit -v`
Expected: all PASS.

- [ ] **Step 8: Update `run.sh`**

Replace the `footprints|emit)` case body and add an `hls)` case and a serve note:

```bash
  footprints|emit)
    # Fetches EMIT L2A footprints and ECOSTRESS swath boxes over CONUS from
    # NASA's CMR (no login), pairs them, then refreshes the HLS store.
    # Rerun to refresh. Network access. "emit" is kept as an alias.
    shift
    python3 -m viz.fetch_emit "$@"
    python3 -m viz.fetch_eco "$@"
    python3 -m viz.coincidence "$@"
    python3 -m viz.fetch_hls
    ;;
  hls)
    # Fetches HLSL30 and HLSS30 v2.0 granule metadata (2022 onward) into
    # data/hls/hls.sqlite, one CSV query per collection-month. The first run is
    # about 635 pages and 15-25 minutes; reruns skip frozen months. Accepts
    # --from YYYY-MM for a shorter first run. Network access.
    shift
    exec python3 -m viz.fetch_hls "$@"
    ;;
```

In the `serve)` case, after the ECOSTRESS note add:

```bash
    if [ ! -f data/hls/hls.sqlite ]; then
      echo "note: no HLS store (data/hls/hls.sqlite); the HLS layer is off until ./run.sh hls is run" >&2
    fi
```

Update the usage line to `{extract|prepare|vendor|footprints|emit|hls|serve|test}`.

- [ ] **Step 9: Check the script parses and the suite is green**

Run: `bash -n run.sh && ./run.sh test 2>&1 | tail -3`
Expected: `OK` (no failures). Do not run `./run.sh hls` here; the full fetch is the user's to start.

- [ ] **Step 10: Commit**

```bash
git add viz/cmr.py viz/fetch_emit.py viz/fetch_hls.py run.sh tests/test_cmr.py tests/test_fetch_hls.py
git commit -m "Fetch HLS granule metadata by month into the SQLite store"
```

---

### Task 4: Server routes, point report, and catalog fields

**Files:**
- Modify: `viz/tileserver.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `hls.store_for`, `Store.counts/acquisitions/tiles_geojson/summary`, `TileIndex.covering`, `hls.is_clear`, `hls.nearest_clear`, `emit.week_sunday`, `paths.HLS_DB`.
- Produces: `GET /api/hls/tiles.geojson`; `GET /api/hls/counts?start&end&cloud&sensor` → `{"counts": {tile: n}}`; `/api/point` accepts `start`, `end`, `cloud`, `sensor`, `window` and returns `hls` (`{start, end, cloud, sensor, window, tiles: [{tile, clear, acq: [{date, time, sensor, cloud}]}]}` or `null`) and `emit[i].hls` (`{date, sensor, cloud, dt}` or `null`); catalog `hls_count`, `hls_tiles`, `hls_fetched`.

- [ ] **Step 1: Extend the fixture in `tests/test_server.py`**

In `ServerTestCase.setUpClass`, add `paths.HLS_DB` to `cls._saved` (both the save tuple and the restore tuple in `tearDownClass`), and set `paths.HLS_DB = paths.DATA / "hls" / "hls.sqlite"` beside the other path overrides. After the coincidence block and before `cls.server = ...`, add:

```python
        # An HLS store with one tile over the fixture centre and one far away.
        from viz import hls

        def acq(ur, start, cloud):
            sensor, tile = hls.parse_ur(ur)
            return {"id": ur, "tile": tile, "date": start[:10], "time": start, "sensor": sensor, "cloud": cloud}

        hls_store = hls.Store(paths.HLS_DB)
        hls_store.replace_month("S30", "2025-07", [
            acq("HLS.S30.T99ZZZ.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10),
            acq("HLS.S30.T99ZZZ.2025204T170000.v2.0", "2025-07-23T17:00:00Z", 10),
            acq("HLS.S30.T98ZZZ.2025201T170000.v2.0", "2025-07-20T17:00:00Z", 0),
        ], "2025-08-02T00:00:00+00:00")
        hls_store.replace_month("L30", "2025-07", [
            acq("HLS.L30.T99ZZZ.2025202T160000.v2.0", "2025-07-21T16:00:00Z", 80),
        ], "2025-08-02T00:00:00+00:00")
        hls_store.put_tile("T99ZZZ", square(cls.lon, cls.lat, 0.5))
        hls_store.put_tile("T98ZZZ", square(cls.lon + 20, cls.lat, 0.5))
        hls_store.close()
```

- [ ] **Step 2: Write the failing route tests**

Append to `tests/test_server.py` before `class TestInterfaceAssets`:

```python
class TestHlsRoutes(ServerTestCase):
    HLS_QUERY = "&start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL&window=7"

    def test_tiles_are_served_as_geojson(self):
        status, ctype, body = self.get("/api/hls/tiles.geojson")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/geo+json")
        tiles = [f["properties"]["tile"] for f in json.loads(body)["features"]]
        self.assertEqual(tiles, ["T98ZZZ", "T99ZZZ"])

    def test_counts_apply_range_cloud_and_sensor(self):
        _, ctype, body = self.get("/api/hls/counts?start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL")
        self.assertEqual(ctype, "application/json")
        self.assertEqual(json.loads(body), {"counts": {"T99ZZZ": 2, "T98ZZZ": 1}})
        _, _, body = self.get("/api/hls/counts?start=2025-07-15&end=2025-07-31&cloud=30&sensor=L30")
        self.assertEqual(json.loads(body), {"counts": {}})
        _, _, body = self.get("/api/hls/counts?start=2025-07-19&end=2025-07-22&cloud=100&sensor=ALL")
        self.assertEqual(json.loads(body), {"counts": {"T99ZZZ": 1, "T98ZZZ": 1}})

    def test_counts_reject_bad_parameters(self):
        for query in ("start=2025-07-15&end=2025-07-31&cloud=30&sensor=X30",
                      "start=2025-7-15&end=2025-07-31&cloud=30&sensor=ALL",
                      "start=2025-07-15&end=2025-07-31&cloud=abc&sensor=ALL",
                      "end=2025-07-31&cloud=30&sensor=ALL"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/hls/counts?" + query)
            self.assertEqual(ctx.exception.code, 400, query)

    def test_catalog_reports_hls_fields(self):
        catalog = json.loads(self.get("/api/catalog")[2])
        self.assertEqual(catalog["hls_count"], 4)
        self.assertEqual(catalog["hls_tiles"], 2)
        self.assertEqual(catalog["hls_fetched"], "2025-08-02T00:00:00+00:00")

    def test_point_lists_covering_tiles_with_clear_counts(self):
        report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        block = report["hls"]
        self.assertEqual((block["start"], block["end"], block["cloud"], block["sensor"], block["window"]),
                         ("2025-07-15", "2025-07-31", 30, "ALL", 7))
        self.assertEqual(len(block["tiles"]), 1)
        tile = block["tiles"][0]
        self.assertEqual(tile["tile"], "T99ZZZ")
        self.assertEqual(tile["clear"], 2)
        self.assertEqual([a["date"] for a in tile["acq"]], ["2025-07-18", "2025-07-21", "2025-07-23"])
        self.assertEqual(tile["acq"][1], {"date": "2025-07-21", "time": "2025-07-21T16:00:00Z",
                                          "sensor": "L30", "cloud": 80})

    def test_point_tags_emit_scenes_with_the_nearest_clear_acquisition(self):
        report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        by_id = {g["id"]: g for g in report["emit"]}
        self.assertEqual(by_id["near-new"]["hls"], {"date": "2025-07-18", "sensor": "S30", "cloud": 10, "dt": -2})
        self.assertIsNone(by_id["near-old"]["hls"])
        # The sensor filter applies to the tag: with S30 excluded only the cloudy L30 row remains.
        report = json.loads(self.get(self.point_url() + "&start=2025-07-15&end=2025-07-31&cloud=30&sensor=L30&window=7")[2])
        self.assertIsNone({g["id"]: g for g in report["emit"]}["near-new"]["hls"])
        self.assertEqual(report["hls"]["tiles"][0]["clear"], 0)

    def test_point_defaults_the_range_to_the_week_window(self):
        # 2024 week 30 ends Sunday 2024-07-28; ±7 days is 07-21 to 08-04.
        report = json.loads(self.get(self.point_url() + "&week=30")[2])
        self.assertEqual((report["hls"]["start"], report["hls"]["end"]), ("2024-07-21", "2024-08-04"))
        self.assertEqual(report["hls"]["tiles"][0]["acq"], [])

    def test_missing_store_gives_404_null_block_and_zero_fields(self):
        # A tagged request first: if hls_block wrote onto the FootprintIndex's own
        # dicts, the tag would survive into the no-store response below.
        tagged = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        self.assertIsNotNone({g["id"]: g for g in tagged["emit"]}["near-new"]["hls"])
        moved = paths.HLS_DB.with_name("moved.sqlite")
        paths.HLS_DB.rename(moved)
        try:
            for route in ("/api/hls/tiles.geojson", "/api/hls/counts?start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL"):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.get(route)
                self.assertEqual(ctx.exception.code, 404)
                self.assertIn("run.sh hls", ctx.exception.read().decode())
            report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
            self.assertIsNone(report["hls"])
            for g in report["emit"]:
                self.assertNotIn("hls", g)
            catalog = json.loads(self.get("/api/catalog")[2])
            self.assertEqual((catalog["hls_count"], catalog["hls_tiles"], catalog["hls_fetched"]), (0, 0, None))
        finally:
            moved.rename(paths.HLS_DB)
```

`point_report` has no direct callers outside `_handle_point`, so its signature change touches nothing else.

- [ ] **Step 3: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestHlsRoutes -v`
Expected: FAIL (404 on the new routes; `KeyError: 'hls'`).

- [ ] **Step 4: Implement in `viz/tileserver.py`**

Change the import line to `from viz import coincidence, color, emit, hls, naming, paths, rasters` and add near the regex constants:

```python
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HLS_SENSORS = ("ALL", "L30", "S30")
```

Add a module-level function after `point_report`'s definition (or before it; keep both at module level):

```python
def hls_block(lon, lat, tile_index, store, granules, start, end, cloud, sensor, window):
    """The point's HLS tiles over [start, end], and a nearest-clear tag on each EMIT granule.

    granules are copies, never the index's own dicts, so the tag does not
    poison the shared FootprintIndex.
    """
    tiles = tile_index.covering(lon, lat)
    block = {"start": start, "end": end, "cloud": cloud, "sensor": sensor, "window": window, "tiles": []}
    rows = store.acquisitions(tiles, start, end)
    for tile in tiles:
        acq = [{"date": r["date"], "time": r["time"], "sensor": r["sensor"], "cloud": r["cloud"]}
               for r in rows if r["tile"] == tile]
        clear = sum(1 for r in acq if hls.is_clear(r, cloud, sensor))
        block["tiles"].append({"tile": tile, "clear": clear, "acq": acq})

    dated = [g for g in granules if g.get("start")]
    if tiles and dated:
        first = min(g["start"][:10] for g in dated)
        last = max(g["start"][:10] for g in dated)
        lo = (datetime.date.fromisoformat(first) - datetime.timedelta(days=window)).isoformat()
        hi = (datetime.date.fromisoformat(last) + datetime.timedelta(days=window)).isoformat()
        clear_rows = [r for r in store.acquisitions(tiles, lo, hi) if hls.is_clear(r, cloud, sensor)]
    else:
        clear_rows = []
    for g in granules:
        g["hls"] = hls.nearest_clear(clear_rows, g["start"][:10], window) if g.get("start") else None
    return block
```

Add `import datetime` to the imports. In `point_report`, change the signature to
`def point_report(lon, lat, crop, year, cdl_year, week=None, hls_params=None):` and replace
`granules = index.covering(lon, lat) if index else []` with
`granules = [dict(g) for g in index.covering(lon, lat)] if index else []`. After the `sunday` block and before the `return`, add:

```python
    hls_report = None
    entry = hls.store_for(paths.HLS_DB)
    if entry is not None:
        store, tile_index = entry
        params = dict(hls_params or {})
        if not (params.get("start") and params.get("end")):
            if sunday:
                centre = datetime.date.fromisoformat(sunday)
                span = datetime.timedelta(days=params.get("window", 7))
                params["start"] = (centre - span).isoformat()
                params["end"] = (centre + span).isoformat()
            else:
                params["start"] = params["end"] = "0000-00-00"   # nothing matches; tiles list stays empty
        hls_report = hls_block(lon, lat, tile_index, store, granules,
                               params["start"], params["end"],
                               params.get("cloud", 30), params.get("sensor", "ALL"), params.get("window", 7))
```

and add `"hls": hls_report,` to the returned dict.

In `do_GET`, after the ECOSTRESS route, add:

```python
            if route == "/api/hls/tiles.geojson":
                entry = hls.store_for(paths.HLS_DB)
                if entry is None:
                    return self._fail(HTTPStatus.NOT_FOUND, "no HLS store; run ./run.sh hls")
                return self._send(json.dumps(entry[0].tiles_geojson()).encode(), CONTENT_TYPES[".geojson"])

            if route == "/api/hls/counts":
                return self._handle_hls_counts(query)
```

In the catalog route, after the `coincident_15min` line:

```python
                summary = hls.store_for(paths.HLS_DB)
                summary = summary[0].summary() if summary else {"count": 0, "fetched": None, "tiles": 0}
                catalog["hls_count"] = summary["count"]
                catalog["hls_tiles"] = summary["tiles"]
                catalog["hls_fetched"] = summary["fetched"]
```

Add two handler methods:

```python
    def _hls_params(self, query, required):
        """Validated HLS parameters, or (None, message). Missing optional ones take defaults."""
        params = {}
        for key in ("start", "end"):
            if key in query:
                value = query[key][0]
                if not DATE_RE.match(value):
                    return None, f"{key} must be YYYY-MM-DD"
                params[key] = value
            elif required:
                return None, "required: start, end"
        try:
            params["cloud"] = int(query["cloud"][0]) if "cloud" in query else 30
            params["window"] = int(query["window"][0]) if "window" in query else 7
        except ValueError:
            return None, "cloud and window must be integers"
        if not (0 <= params["cloud"] <= 100) or not (0 <= params["window"] <= 60):
            return None, "cloud must be 0-100 and window 0-60"
        params["sensor"] = query["sensor"][0] if "sensor" in query else "ALL"
        if params["sensor"] not in HLS_SENSORS:
            return None, "sensor must be ALL, L30, or S30"
        return params, None

    def _handle_hls_counts(self, query):
        entry = hls.store_for(paths.HLS_DB)
        if entry is None:
            return self._fail(HTTPStatus.NOT_FOUND, "no HLS store; run ./run.sh hls")
        params, message = self._hls_params(query, required=True)
        if params is None:
            return self._fail(HTTPStatus.BAD_REQUEST, message)
        counts = entry[0].counts(params["start"], params["end"], params["cloud"], params["sensor"])
        self._send(json.dumps({"counts": counts}).encode(), CONTENT_TYPES[".json"])
```

In `_handle_point`, before `report = point_report(...)`, add:

```python
        hls_params, message = self._hls_params(query, required=False)
        if hls_params is None:
            return self._fail(HTTPStatus.BAD_REQUEST, message)
```

and pass `hls_params=hls_params` to `point_report`.

- [ ] **Step 5: Run the whole suite**

Run: `./run.sh test 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 6: Smoke the real server on a spare port**

Run:

```bash
PYTHONNOUSERSITE=1 python3 -m viz.tileserver --port 8765 & sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/api/catalog
curl -s http://127.0.0.1:8765/api/catalog | python3 -c "import json,sys; c=json.load(sys.stdin); print(c['hls_count'], c['hls_tiles'], c['hls_fetched'])"
kill %1
```

Expected: `200` and `0 0 None` (no store yet) or the real counts if the user has already fetched.

- [ ] **Step 7: Commit**

```bash
git add viz/tileserver.py tests/test_server.py
git commit -m "Serve HLS tiles, counts, and per-point acquisitions with EMIT tags"
```

---

### Task 5: Shared time window, HLS controls, tile layer, counts, and legend

**Files:**
- Modify: `viz/web/index.html`
- Modify: `viz/web/style.css`
- Modify: `viz/web/app.js`
- Modify: `tests/test_server.py` (`TestInterfaceAssets`)

**Interfaces:**
- Consumes: `/api/hls/tiles.geojson`, `/api/hls/counts`, catalog `hls_count`.
- Produces: `state.days` (replaces `state.emitWindow`), `windowBounds()` (replaces `emitWindowBounds()`), `hlsRange()`, `isoDate(ms)`, `HLS_CLASSES`, `hlsColour(n)`, `hlsStyleFor(counts)`, `loadHls()`, `syncHls()`, `refreshHlsCounts()`, `drawHlsLegend()`; element ids `timeWindow`, `timeWindowOut`, `hls`, `hlsControls`, `hlsRange`, `hlsCloud`, `hlsCloudOut`, `hlsLegend`; radio names `hlsMode`, `hlsSensor`.

- [ ] **Step 1: Write the failing static and runtime tests**

In `test_emit_layer_controls_and_canvas_renderer` (tests/test_server.py:547), change the literal `'id="emitWindow"'` to `'id="timeWindow"'`; it is the only test that names the slider (`test_emit_within_window_only_toggle` does not and stays as it is). In `test_catalog_is_json_with_expected_keys` nothing changes, since it checks presence only. Then append to `TestInterfaceAssets`:

```python
    def test_time_window_is_shared_above_the_emit_block(self):
        _, _, index = self.get("/")
        html = index.decode()
        self.assertIn('id="timeWindow"', html)
        self.assertNotIn('id="emitWindow"', html)
        self.assertLess(html.index('id="timeWindow"'), html.index('id="emit"'))
        self.assertIn("Time window", html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("function windowBounds()", text)
        self.assertNotIn("emitWindowBounds", text)
        self.assertIn("state.days", text)

    def test_hls_layer_controls_and_routes(self):
        _, _, index = self.get("/")
        html = index.decode()
        for ident in ('id="hls"', 'name="hlsMode"', 'id="hlsRange"', 'id="hlsCloud"',
                      'name="hlsSensor"', 'id="hlsLegend"'):
            self.assertIn(ident, html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("/api/hls/tiles.geojson", text)
        self.assertIn("/api/hls/counts?", text)
        self.assertIn("HLS_CLASSES", text)
        for colour in ("#e7d4e8", "#c2a5cf", "#9970ab", "#762a83", "#40004b"):
            self.assertIn(colour, text)
        self.assertIn('map.createPane("hls")', text)
        self.assertIn("451", text[text.index('map.getPane("hls")'):text.index('map.getPane("hls")') + 80])
        self.assertIn("setTimeout(fetchHlsCounts, 150)", text)

    def test_hls_range_resolves_week_window_and_season(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        if not shutil.which("node"):
            self.skipTest("node not available")
        parts = []
        for name in ("weekSunday(year, week)", "isoDate(ms)", "hlsRange()"):
            match = re.search(r"function " + re.escape(name) + r" \{.*?\n  \}", text, re.S)
            self.assertIsNotNone(match, name + " not found in app.js")
            parts.append(match.group(0))
        script = (
            'var state = { week: 30, year: 2025, days: 7, hlsMode: "week", crop: "corn", var: "cond" };\n'
            "function weeksFor() { return [14, 30, 44]; }\n" + "\n".join(parts) + "\n"
            "var a = hlsRange(); state.hlsMode = \"season\"; var b = hlsRange();\n"
            "state.days = 0; state.hlsMode = \"week\"; var c = hlsRange();\n"
            "state.week = null; var d = hlsRange();\n"
            "console.log([a.start, a.end, b.start, b.end, c.start, c.end, String(d)].join(\"|\"));"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         "2025-07-20|2025-08-03|2025-03-31|2025-11-02|2025-07-27|2025-07-27|null")
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestInterfaceAssets -v`
Expected: the three new tests FAIL; the two edited ones FAIL on `timeWindow`.

- [ ] **Step 3: Edit `viz/web/index.html`**

Remove the `Window ±days` label (the `emitWindow` slider) from `#emitControls`. Insert directly before the `<label class="inline"><input type="checkbox" id="emit"> EMIT footprints</label>` line:

```html
    <label>Time window ±days <output id="timeWindowOut">7</output>
      <input type="range" id="timeWindow" min="0" max="30" step="1" value="7">
      <span class="note">Around the selected CPC week's Sunday; drives the EMIT highlight and the HLS week window.</span>
    </label>
```

Insert directly after the closing `</div>` of `#ecoControls`:

```html
    <label class="inline"><input type="checkbox" id="hls"> HLS coverage</label>
    <div class="sub" id="hlsControls">
      <div class="inline">Mode:
        <label class="inline"><input type="radio" name="hlsMode" value="week" checked> Week window</label>
        <label class="inline"><input type="radio" name="hlsMode" value="season"> Season</label>
      </div>
      <div class="note" id="hlsRange"></div>
      <label>Max cloud <output id="hlsCloudOut">30%</output>
        <input type="range" id="hlsCloud" min="0" max="100" step="5" value="30">
      </label>
      <div class="inline">Sensor:
        <label class="inline"><input type="radio" name="hlsSensor" value="ALL" checked> Both</label>
        <label class="inline"><input type="radio" name="hlsSensor" value="L30"> L30</label>
        <label class="inline"><input type="radio" name="hlsSensor" value="S30"> S30</label>
      </div>
      <div id="hlsLegend"></div>
    </div>
```

- [ ] **Step 4: Edit `viz/web/style.css`**

Append:

```css
/* HLS coverage */
#hlsControls { display: grid; gap: 7px; padding-left: 22px; }
#hlsControls.disabled { opacity: 0.5; }
#hlsControls .note { margin: 0; font-size: 11px; }
#hlsLegend { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 11px; color: var(--muted); }
#hlsLegend span::before {
  content: ""; display: inline-block; width: 14px; height: 10px; margin-right: 4px;
  background: var(--swatch); border: 1px solid #40004b; vertical-align: middle; opacity: 0.8;
}
#hlsLegend span.zero::before { background: #fff; opacity: 0.4; }
.leaflet-hls-pane { cursor: default; }
label .note { font-weight: 450; }
```

- [ ] **Step 5: Edit `viz/web/app.js`**

(a) In `state`, replace `emitWindow: 7,` with `days: 7,` and add after `ecoDay: "DAY"`:

```js
    ,
    hls: false,
    hlsMode: "week",
    hlsCloud: 30,
    hlsSensor: "ALL"
```

(b) Rename every `state.emitWindow` to `state.days` and every `emitWindowBounds` to `windowBounds` (the function definition included). Rename the element ids `emitWindow` → `timeWindow` and `emitWindowOut` → `timeWindowOut` in `syncEmit` and `wire`. In `syncEmit`, the slider must no longer be disabled with the EMIT layer: change `el("emitCloud").disabled = el("emitWindow").disabled = !(available && state.emit);` to `el("emitCloud").disabled = !(available && state.emit);`.

(c) After the ECOSTRESS pane block (after `var ecoLoaded = false;`), add:

```js
  map.createPane("hls");
  map.getPane("hls").style.zIndex = 451;   // above CPC, below ECOSTRESS and EMIT
  var hlsRenderer = L.canvas({ pane: "hls" });
  // Sequential purples, away from the CDL crop colours and both CPC ramps.
  var HLS_CLASSES = [
    { min: 1, max: 1, colour: "#e7d4e8", label: "1" },
    { min: 2, max: 2, colour: "#c2a5cf", label: "2" },
    { min: 3, max: 4, colour: "#9970ab", label: "3–4" },
    { min: 5, max: 8, colour: "#762a83", label: "5–8" },
    { min: 9, max: Infinity, colour: "#40004b", label: "9+" }
  ];
  var HLS_OUTLINE = "#40004b";
  var hlsLayer = null;
  var hlsLoaded = false;
  var hlsCounts = {};
  var hlsTimer = null;
  var hlsRequest = 0;

  function hlsColour(n) {
    for (var i = 0; i < HLS_CLASSES.length; i++) {
      if (n >= HLS_CLASSES[i].min && n <= HLS_CLASSES[i].max) { return HLS_CLASSES[i].colour; }
    }
    return null;
  }
```

(d) After `windowBounds()` (the renamed function), add:

```js
  function isoDate(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  // The HLS counting range: the shared ±days window around the CPC week's Sunday,
  // or the crop's reporting season (Monday of its first CPC week to Sunday of its last).
  function hlsRange() {
    if (state.week === null || state.week === undefined) { return null; }
    if (state.hlsMode === "season") {
      var weeks = weeksFor(state.crop, state.var, state.year);
      if (!weeks.length) { return null; }
      var first = weekSunday(state.year, weeks[0]).getTime() - 6 * 86400000;
      var last = weekSunday(state.year, weeks[weeks.length - 1]).getTime();
      return { start: isoDate(first), end: isoDate(last) };
    }
    var centre = weekSunday(state.year, state.week).getTime();
    var span = state.days * 86400000;
    return { start: isoDate(centre - span), end: isoDate(centre + span) };
  }
```

(e) After `drawEcoLegend`, add:

```js
  function hlsStyleFor(counts) {
    return function (feature) {
      var n = counts[feature.properties.tile] || 0;
      var colour = hlsColour(n);
      return {
        stroke: true, interactive: true,
        color: HLS_OUTLINE, weight: 0.5, opacity: n ? 0.8 : 0.25,
        fill: !!colour, fillColor: colour || "#ffffff", fillOpacity: colour ? 0.55 : 0
      };
    };
  }

  function applyHlsCounts() {
    if (!hlsLayer) { return; }
    hlsLayer.setStyle(hlsStyleFor(hlsCounts));
    hlsLayer.eachLayer(function (layer) {
      var tile = layer.feature.properties.tile;
      layer.setTooltipContent(tile + ": " + (hlsCounts[tile] || 0) + " clear");
    });
    drawHlsLegend();
  }

  function fetchHlsCounts() {
    var range = hlsRange();
    if (!hlsLayer || !state.hls || !range) { return; }
    var seq = ++hlsRequest;
    fetch("/api/hls/counts?start=" + range.start + "&end=" + range.end +
          "&cloud=" + state.hlsCloud + "&sensor=" + state.hlsSensor)
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (seq !== hlsRequest) { return; }   // a newer request has superseded this one
        hlsCounts = body.counts || {};
        applyHlsCounts();
      })
      .catch(function () { /* keep the last counts */ });
  }

  function refreshHlsCounts() {
    clearTimeout(hlsTimer);
    hlsTimer = setTimeout(fetchHlsCounts, 150);
  }

  function loadHls() {
    if (hlsLoaded || !(state.catalog.hls_count > 0)) { return; }
    hlsLoaded = true;
    fetch("/api/hls/tiles.geojson").then(function (r) { return r.json(); }).then(function (geo) {
      hlsLayer = L.geoJSON(geo, {
        pane: "hls", renderer: hlsRenderer, style: hlsStyleFor(hlsCounts),
        onEachFeature: function (f, layer) {
          layer.bindTooltip(f.properties.tile + ": 0 clear", { sticky: true, className: "emit-tip" });
        }
      });
      syncHls();
    }).catch(function () { hlsLoaded = false; });
  }

  function drawHlsLegend() {
    var range = hlsRange();
    el("hlsRange").textContent = range
      ? (state.hlsMode === "season" ? "Season " : "Window ") + range.start + " to " + range.end
      : "No CPC weeks for this selection";
    el("hlsLegend").innerHTML =
      '<span class="zero" style="--swatch:#fff">0</span>' +
      HLS_CLASSES.map(function (c) {
        return '<span style="--swatch:' + c.colour + '">' + c.label + "</span>";
      }).join("") + "<span>clear acquisitions per MGRS tile</span>";
  }

  function syncHls() {
    var available = state.catalog.hls_count > 0;
    var box = el("hls");
    box.disabled = !available;
    box.parentNode.title = available ? "" : "No HLS store; run ./run.sh hls";
    if (!available && state.hls) { state.hls = false; box.checked = false; }
    var active = available && state.hls;
    el("hlsControls").classList.toggle("disabled", !active);
    el("hlsCloud").disabled = !active;
    ["hlsMode", "hlsSensor"].forEach(function (name) {
      Array.prototype.forEach.call(document.getElementsByName(name), function (radio) {
        radio.disabled = !active;
      });
    });
    if (!state.hls && hlsLayer && map.hasLayer(hlsLayer)) { map.removeLayer(hlsLayer); }
    if (state.hls) {
      if (!hlsLayer) { loadHls(); return; }
      if (!map.hasLayer(hlsLayer)) { hlsLayer.addTo(map); }
      drawHlsLegend();
      refreshHlsCounts();
    }
  }
```

(f) In `refresh()`, add `syncHls();` after `syncEco();`.

(g) In `wire()`, update the window slider handler to:

```js
    el("timeWindow").addEventListener("input", function (e) {
      state.days = Number(e.target.value);
      el("timeWindowOut").textContent = e.target.value === "0" ? "off" : e.target.value;
      syncEmit();
      syncEco();
      if (state.hls) { drawHlsLegend(); refreshHlsCounts(); }
    });
```

and add after the `ecoDay` wiring:

```js
    el("hls").addEventListener("change", function (e) { state.hls = e.target.checked; syncHls(); });
    el("hlsCloud").addEventListener("input", function (e) {
      state.hlsCloud = Number(e.target.value);
      el("hlsCloudOut").textContent = e.target.value + "%";
      refreshHlsCounts();
    });
    ["hlsMode", "hlsSensor"].forEach(function (name) {
      Array.prototype.forEach.call(document.getElementsByName(name), function (radio) {
        radio.addEventListener("change", function (e) {
          if (!e.target.checked) { return; }
          state[name] = e.target.value;
          drawHlsLegend();
          refreshHlsCounts();
        });
      });
    });
```

(h) In the boot block, change `drawEmitLegend(); drawEcoLegend(); syncEmit(); syncEco();` to `drawEmitLegend(); drawEcoLegend(); drawHlsLegend(); syncEmit(); syncEco(); syncHls();`.

Note that the ECOSTRESS highlight also reads the window, which is why the slider handler calls `syncEco()`; check whether the existing handler already did so and keep that behaviour.

- [ ] **Step 6: Syntax check and run the suite**

Run: `node --check viz/web/app.js && ./run.sh test 2>&1 | tail -3`
Expected: no syntax error, `OK`.

- [ ] **Step 7: Commit**

```bash
git add viz/web/index.html viz/web/style.css viz/web/app.js tests/test_server.py
git commit -m "Add the HLS coverage layer with a shared time window"
```

---

### Task 6: Readout block and EMIT tags

**Files:**
- Modify: `viz/web/app.js` (`showReadout`, the click handler)
- Modify: `tests/test_server.py` (`TestInterfaceAssets`)

**Interfaces:**
- Consumes: `/api/point` `hls` block and `emit[i].hls` from Task 4; `hlsRange()`, `state.hlsCloud`, `state.hlsSensor`, `state.days` from Task 5.
- Produces: `formatDays(dt)`; the popup's HLS section and per-scene tags.

- [ ] **Step 1: Write the failing tests**

Append to `TestInterfaceAssets`:

```python
    def test_readout_lists_hls_acquisitions_and_tags_emit_scenes(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("HLS acquisitions", text)
        self.assertIn("clear of", text)
        self.assertIn("no clear HLS within", text)
        self.assertIn("&start=", text)
        self.assertIn("&window=", text)
        self.assertIn("slice(0, 20)", text)
        if not shutil.which("node"):
            self.skipTest("node not available")
        match = re.search(r"function formatDays\(dt\) \{.*?\n  \}", text, re.S)
        self.assertIsNotNone(match, "formatDays function not found in app.js")
        script = match.group(0) + "\nconsole.log([0, -2, 3, 1].map(formatDays).join(\"|\"));"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "same day|−2 d|+3 d|+1 d")
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestInterfaceAssets.test_readout_lists_hls_acquisitions_and_tags_emit_scenes -v`
Expected: FAIL on `HLS acquisitions`.

- [ ] **Step 3: Implement**

(a) After `formatDt`, add:

```js
  function formatDays(dt) {
    if (dt === 0) { return "same day"; }
    return (dt < 0 ? "−" : "+") + Math.abs(dt) + " d";
  }
```

(b) In `showReadout`, inside the EMIT list's `map` callback, after the ECOSTRESS tag expression and before `(inWin ? " <span>★</span>" : "")`, add:

```js
               (g.hls
                 ? ' <span class="eco-tag">HLS ' + g.hls.sensor + " " + formatDays(g.hls.dt) + ", " +
                   (g.hls.cloud === null ? "?" : g.hls.cloud) + "% cloud</span>"
                 : (report.hls ? ' <span class="eco-tag">no clear HLS within ±' + report.hls.window + " d</span>" : "")) +
```

(c) After the ECOSTRESS block in `showReadout` (after its `and N more` handling, before `return html;`), add:

```js
    var hlsBlock = report.hls;
    if (hlsBlock) {
      var totalAcq = 0, totalClear = 0;
      hlsBlock.tiles.forEach(function (t) { totalAcq += t.acq.length; totalClear += t.clear; });
      html += '<p class="section">HLS acquisitions, ' + hlsBlock.start + " to " + hlsBlock.end +
              ": " + totalClear + " clear of " + totalAcq + "</p>";
      if (!hlsBlock.tiles.length) {
        html += '<p class="note">No MGRS tile ring covers this point.</p>';
      }
      hlsBlock.tiles.forEach(function (t) {
        html += '<p class="note">' + t.tile + ": " + t.clear + " clear of " + t.acq.length + "</p>";
        var shownH = t.acq.slice(0, 20);
        html += '<ul class="emit-list">' + shownH.map(function (a) {
          var clear = a.cloud !== null && a.cloud <= hlsBlock.cloud &&
                      (hlsBlock.sensor === "ALL" || a.sensor === hlsBlock.sensor);
          return "<li" + (clear ? ' class="in"' : "") + '><span class="when">' + a.date + "</span>" +
                 "<span>" + a.sensor + "</span>" +
                 "<span>" + (a.cloud === null ? "?" : a.cloud + "%") + " cloud</span>" +
                 (clear ? " <span>✓</span>" : "") + "</li>";
        }).join("") + "</ul>";
        if (t.acq.length > shownH.length) {
          html += '<p class="note">and ' + (t.acq.length - shownH.length) + " more</p>";
        }
      });
    }
```

(d) In the map click handler, extend the URL:

```js
      var range = hlsRange();
      var url = "/api/point?lon=" + e.latlng.lng.toFixed(6) +
                "&lat=" + e.latlng.lat.toFixed(6) +
                "&crop=" + state.crop + "&year=" + state.year + "&cdl_year=" + state.cdlYear +
                (state.week !== null ? "&week=" + state.week : "") +
                (range ? "&start=" + range.start + "&end=" + range.end : "") +
                "&cloud=" + state.hlsCloud + "&sensor=" + state.hlsSensor + "&window=" + state.days;
```

- [ ] **Step 4: Syntax check and run the suite**

Run: `node --check viz/web/app.js && ./run.sh test 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add viz/web/app.js tests/test_server.py
git commit -m "List HLS acquisitions in the readout and tag EMIT scenes"
```

---

## Self-Review

**Spec coverage.** §3.1 store → Task 2. §3.2 fetch (CSV by month, CMR-Hits paging, frozen rule, month filter, ring queries, `./run.sh hls`, `footprints` append, `serve` note) → Task 3. §3.3 routes, point block, EMIT tags, catalog fields, thread-local read-only store, copies of index dicts → Tasks 2 and 4. §3.4 controls, shared window, season resolution, pane 451, ramp, legend, tooltip, debounce, geometry fetched once → Task 5. §3.5 readout and tags → Task 6. §4 tests: frozen rule at 59/60/61 (Task 1), idempotent month replacement, counts boundaries, ordering, nearest-clear tie (Task 2), paging with a fake fetcher and missing polygon (Task 3), routes and 404s and catalog (Task 4), static and node asserts (Tasks 5–6).

**Placeholder scan.** None.

**Type consistency.** `hls.parse_ur` returns `(sensor, tile)` and is used that way in Tasks 3 and 4's fixtures. `Store.acquisitions` rows carry `tile, date, time, sensor, cloud` and `hls_block` reads exactly those. `store_for` returns `(Store, TileIndex)` and both server call sites index `[0]` / unpack. `state.days`, `windowBounds`, `timeWindow`, `hlsRange` are named identically in Tasks 5 and 6. `report.hls.window` is the server's echoed `window` from Task 4.
