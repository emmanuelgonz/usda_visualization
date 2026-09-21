# HLS Coverage Layer — Design

**Date:** 2026-09-21
**Status:** Approved design, pending implementation plan
**Extends:** `2026-09-21-ecostress-coincidence-design.md`

## 1. Purpose

Show how many clear Harmonized Landsat Sentinel-2 (HLS) acquisitions each MGRS tile holds for the
selected CPC week window or for the crop's reporting season, list those acquisitions on click, and
tag each EMIT scene in the readout with its nearest clear HLS acquisition. HLS is the 30 m
surface-reflectance series behind crop phenology work, so the layer answers "how dense is the
clear optical record here around this week" and stops short of downloading reflectance.

## 2. Source data (verified 2026-09-21)

HLS version 2.0 has two collections at the LP DAAC: `HLSL30` (Landsat 8 and 9) and `HLSS30`
(Sentinel-2A, B, and C), both tiled on the 109.8 km MGRS grid. CMR holds one granule per tile per
acquisition. Counts over the CONUS bounding box (125°W–66.9°W, 24.4°N–49.4°N) from 2022-01-01:

| Collection | Granules 2022 onward | Busiest month (Jul 2025) |
| --- | --- | --- |
| HLSL30 | 549,095 | 10,645 |
| HLSS30 | 719,618 | 16,623 |
| Total | 1,268,713 | 27,268 |

Each granule carries `cloud_cover` as an integer percent (no blanks in a 2,000-row sample), a
`time_start`, a title of the form `HLS.S30.T15TVH.2025203T170849.v2.0` whose third dot-separated
field is the MGRS tile ID, and a `polygons` ring of five or six vertices in latitude-longitude
order. About 900 distinct tiles cover CONUS.

Two CMR limits shape the fetch. The JSON granule endpoint returns 15.5 KB per granule, 95% of it
download links, so a full JSON pull would be about 19.6 GB; the CSV endpoint returns the granule
ID, start time, and cloud cover at 3.1 KB per granule (5.3 MB per 2,000-row page, about 3.9 GB in
total). The CSV lacks the tile ring, which is fetched once per tile from the JSON endpoint by
pattern query (`granule_ur=HLS.S30.<tile>.*`, page size 1, about 0.5 s). Second, CMR refuses any
query whose `page_num × page_size` exceeds 1,000,000 rows, so the fetch splits by collection and
month; the busiest month is 9 pages.

## 3. Architecture

Counting happens on the server, in SQLite, because 1.27 million rows do not belong in the browser;
the tile rings (about 900 features) ship once and the browser recolours them from a small counts
response. This departs from the EMIT and ECOSTRESS layers, which filter their few-tens-of-thousands
of footprints client-side.

| Component | Purpose |
| --- | --- |
| `viz/fetch_hls.py` | Pages CMR by collection and month into `data/hls/hls.sqlite`; fills tile rings |
| `viz/hls.py` | `Store` (schema, month replacement, counts, acquisitions, tile GeoJSON), tile-ID parsing, month ranges, CSV parsing |
| `viz/cmr.py` | Gains `fetch_bytes(url)` with the same retry policy as `fetch_page` |
| `viz/paths.py` | Gains `HLS_DATA` and `HLS_DB` |
| `viz/tileserver.py` | Two HLS routes; `/api/point` gains the HLS block and per-scene tags; catalog fields |
| `viz/web/` | Tile layer, controls, legend block, readout section, relocated window slider |
| `run.sh` | `hls` subcommand; `footprints` runs it after the coincidence step |

### 3.1 Store

`data/hls/hls.sqlite`, opened with the stdlib `sqlite3` module, gitignored like the rasters:

| Table | Columns | Indexes |
| --- | --- | --- |
| `acq` | `id TEXT PRIMARY KEY` (granule UR), `tile TEXT`, `date TEXT` (YYYY-MM-DD, UTC), `time TEXT` (ISO start), `sensor TEXT` (`L30` or `S30`), `cloud INTEGER` | `(date)`, `(tile, date)` |
| `tiles` | `tile TEXT PRIMARY KEY`, `ring TEXT` (JSON array of [lon, lat] pairs, closed) | |
| `months` | `sensor TEXT`, `month TEXT` (YYYY-MM), `count INTEGER`, `fetched_at TEXT` (ISO UTC); primary key `(sensor, month)` | |

The file is about 120 MB. SQLite's default rollback journal lets the server keep reading committed
state while a fetch replaces a month, so no `.part` rename is needed.

### 3.2 Fetch

`./run.sh hls` runs `viz/fetch_hls.py`. For each collection (`HLSL30`, `HLSS30`, version 2.0) and
each month from 2022-01 to the current month, it reads the `CMR-Hits` header from the first CSV
page (`https://cmr.earthdata.nasa.gov/search/granules.csv` with the CONUS box, the month as
`temporal`, and `page_size=2000`), fetches `ceil(hits / 2000)` pages, parses them with the `csv`
module (columns `Granule UR`, `Start Time`, `Cloud Cover`), and replaces that month in one
transaction: delete `acq` rows for the sensor whose `date` falls in the month, insert the new rows,
upsert the `months` row with the count and the fetch time. A row with a blank or non-integer cloud
value is stored with `cloud NULL` and never counts as clear.

A month whose `months` row was written more than 60 days after the month ended is frozen and
skipped on later runs, so the first run is about 635 pages and 15–25 minutes, and a refresh
touches only the current month and the one or two before it. After the CSV pass, every tile in
`acq` with no `tiles` row gets one JSON pattern query; the first run makes about 900 such requests
in roughly eight minutes and later runs make none. A ring whose query returns no polygon is logged
and left absent, so its tile is counted but never drawn.

`./run.sh footprints` appends the HLS step after the coincidence step, so one command refreshes all
three layers. `./run.sh serve` prints a note naming `./run.sh hls` when the database is absent and
starts anyway.

### 3.3 Server

`viz/hls.py` exposes a `Store` with a thread-local read-only connection (`file:...?mode=ro`),
re-opened when the file's modification time changes, mirroring the footprint index cache. Its
queries:

- `counts(start, end, cloud, sensor)`: one `GROUP BY tile` over `date BETWEEN start AND end AND
  cloud <= ?` with an optional sensor filter, returning `{tile: n}`. A two-week window scans
  roughly 15,000 rows; a full season roughly 250,000, well under a second.
- `acquisitions(tiles, start, end)`: rows for the given tiles in the range, oldest first, with
  `date`, `time`, `sensor`, `cloud`.
- `tiles_geojson()`: a FeatureCollection with one polygon per `tiles` row and a `tile` property.
- `summary()`: total count and latest `fetched_at` from `months`, and the tile count.

Point-in-tile uses the existing `FootprintIndex` machinery over the tile rings, built from the
`tiles` table rather than a GeoJSON file, cached with the store. MGRS tiles overlap by about 5 km,
so a point lies in one to four tiles.

Routes:

- `GET /api/hls/tiles.geojson`: the tile collection, or 404 with a message naming `./run.sh hls`.
- `GET /api/hls/counts?start=YYYY-MM-DD&end=YYYY-MM-DD&cloud=30&sensor=ALL|L30|S30`: `{"counts":
  {"T15TVH": 4, ...}}`, tiles with zero omitted. 400 on a malformed date or unknown sensor.
- `GET /api/point` gains optional `start`, `end`, `cloud`, `sensor`, and `window` (days). When
  the database is present it returns `hls` with `tiles`, a list of `{tile, clear, acq: [{date,
  time, sensor, cloud}]}` for each covering tile over `[start, end]`, where `clear` counts rows
  with `cloud <= cloud` and the sensor filter applied. Each `emit` entry gains `hls`: the clear
  acquisition (same cloud and sensor filters, any covering tile) with the smallest `|dt|` within
  `±window` days of the scene's start, as `{date, sensor, cloud, dt}` with `dt` a signed whole
  number of days (HLS minus EMIT), or `null`. Without the database, `hls` is `null` and the
  per-scene tag is absent.

The catalog gains `hls_count`, `hls_tiles`, and `hls_fetched` (ISO timestamp or null), computed
from `months` and `tiles` on each request.

### 3.4 Layer and controls

An "HLS coverage" checkbox, off by default so the map opens unchanged, disabled with a note when
the catalog reports no rows. Beneath it: a Mode radio "Week window" (default) / "Season"; a "Max
cloud" slider from 0 to 100%, default 30, which also defines "clear" everywhere in this feature;
and a Sensor radio Both (default) / L30 / S30.

The "Window ±days" slider moves out of the EMIT block into a "Time window" block placed above the
EMIT block. Its value drives both the EMIT highlight and the HLS week window, with the same
±N-day span around the selected CPC week's Sunday; 0 still disables the EMIT highlight, and in HLS
week mode 0 counts the Sunday alone. Season resolves to the Monday of the first CPC week to the
Sunday of the last week present in the catalog for the selected crop, variable, and year, and the
resolved range is printed beside the radio.

Tiles draw as a GeoJSON layer on a Canvas renderer in a new pane between the CPC layer and the
ECOSTRESS swaths, filled at opacity 0.55 with a five-class sequential ramp (1, 2, 3–4, 5–8, 9 or
more) chosen at implementation to stay distinct from the CDL crop colours and the CPC ramps; a
tile with no clear acquisition draws as a faint outline only. A legend block shows the five
classes and the active range. Hovering shows a tooltip such as "T15TVH: 4 clear". Counts are
refetched, debounced at 150 ms, on any change of week, year, crop, variable, mode, cloud, sensor,
or window; the tile geometry is fetched once when the layer is first enabled. Style functions
return the same key set on every call, as the EMIT layer's do.

### 3.5 Readout

After the ECOSTRESS block the popup gains "HLS acquisitions, <range>: N clear of M", then for each
covering tile a heading with its ID and up to twenty rows of date, sensor, and cloud percentage,
clear rows marked, followed by "and N more" when truncated. The range, cloud threshold, sensor,
and window sent with the point request are the current control values. Each EMIT scene row gains
a tag such as "HLS S30 −2 d, 12% cloud" or "no clear HLS within ±7 d". There is no map-level
EMIT–HLS marking.

## 4. Testing

Unit: tile ID parsing from a granule UR; the month sequence from 2022-01 to a given month; the
frozen rule at 59, 60, and 61 days; CSV parsing against a saved three-row fixture including a blank
cloud value; `Store` schema creation, replacing a month twice yielding the same rows, counts under
each sensor filter and at a cloud boundary, acquisitions ordering, and nearest-clear selection
with a tie broken toward the earlier acquisition; the fetch loop's page count from a fake fetcher
that reports hits and serves pages; the ring query's handling of a missing polygon.

Integration: the tiles route serves a fixture database and 404s without one; the counts route
returns the fixture's counts and 400s on a bad date; the point route returns `hls.tiles` for a
fixture tile containing the fixture centre and `null` without the database, and tags a fixture
EMIT scene with the expected nearest acquisition; the catalog carries the three new fields.

Static: the interface references both HLS routes, carries the mode, cloud, and sensor controls,
and the window slider sits in the "Time window" block; node runtime asserts cover the season range
resolution and the week-window bounds shared with EMIT. The browser check is the user's.

## 5. Out of scope

Downloading HLS reflectance or vegetation-index products, per-pixel cloud masks, map-level HLS
coincidence styling, per-tile time-series charts, and years before 2022.
