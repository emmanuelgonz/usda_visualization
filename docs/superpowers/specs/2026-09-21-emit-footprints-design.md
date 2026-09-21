# EMIT Footprint Layer — Design

**Date:** 2026-09-21
**Status:** Approved design, pending implementation plan
**Extends:** `2026-09-03-usda-cpc-cdl-viz-design.md`

## 1. Purpose

Show where and when NASA's EMIT imaging spectrometer has acquired scenes over CONUS, on the same
map as the CDL and CPC layers, so that a cell showing low crop condition can be checked at once
for EMIT coverage close to that week. This serves a planned research project on hyperspectral
detection of crop stress; the layer answers "is there a scene here, when, and how cloudy" and
stops short of ingesting the reflectance data itself.

## 2. Source data (verified 2026-09-20)

EMIT (Earth Surface Mineral Dust Source Investigation) flies on the International Space Station and
records 285 bands from 380 to 2500 nm at about 60 m in scenes roughly 75 km across. The product
relevant to vegetation is the L2A surface reflectance, collection `EMITL2ARFL` version 001 at the
LP DAAC (version 002 is registered but holds no granules).

NASA's Common Metadata Repository (CMR) returns every granule's footprint polygon, start and end
time, cloud-cover percentage, granule ID, and browse and data links as JSON, filtered by bounding
box and date, without authentication. Downloading the reflectance files needs an Earthdata login
and is out of scope.

Counts over the CONUS bounding box (125°W–66.9°W, 24.4°N–49.4°N), from CMR:

| Year | Granules |
| --- | --- |
| 2022 (August onward) | 417 |
| 2023 | 5,060 |
| 2024 | 7,814 |
| 2025 | 8,309 |
| 2026 to date | 7,278 |
| Total | 28,878 |

For 2025, 1,733 scenes have their centre on a 9 km cell with at least 10% corn, cotton, soy, or
wheat cover, and 1,540 of those were acquired in April through October. Acquisition is targeted
and seasonal, peaking in June and August. Cloud cover averages 57%, so a cloud filter is essential.

## 3. Week-to-date mapping

CPC rasters carry week numbers, not dates. The archive's timestamps fix the mapping: the file for
corn week 15 of 2024 was written on Monday 15 April 2024, the day NASS publishes the report for the
week ending Sunday 14 April, which is ISO week 15. CPC week N is therefore ISO week N of its year,
and the time window is centred on that week's Sunday.

## 4. Architecture

Filtering happens in the browser. All footprints ship once as a GeoJSON file of a few megabytes
and both sliders restyle the layer locally, so they respond instantly; a server-side filter would
cost a round trip per slider tick for no saving. The server's one EMIT duty is answering which
granules cover a clicked point.

| Component | Purpose |
| --- | --- |
| `viz/fetch_emit.py` | Pages CMR and writes `data/emit/footprints.geojson` |
| `viz/emit.py` | Loads the footprints once and answers point-in-polygon queries |
| `viz/tileserver.py` | Serves the footprints file; adds `emit` and `week_sunday` to `/api/point`; computes `emit_count` and `emit_fetched` from the file at request time |
| `viz/web/` | Footprint layer, two sliders, legend block, popup section |

### 4.1 Fetch

`./run.sh emit` runs `viz/fetch_emit.py`, which requests
`https://cmr.earthdata.nasa.gov/search/granules.json` with `short_name=EMITL2ARFL`, the CONUS
bounding box, and `page_size=2000`, advancing `page_num` until a page returns no entries. Each
entry becomes one GeoJSON feature: a polygon from the entry's `polygons` field (CMR lists
coordinates as alternating latitude and longitude, which the script swaps to GeoJSON order) and
properties `id` (the granule title), `start`, `end`, `cloud` (float percent), `year`, `browse`
(the first browse-image link), and `data` (the first data or metadata link). Every run refetches
in full, about fifteen requests, so refreshing is running it again. The file is data, not code,
and is gitignored like the rasters. This is the project's third sanctioned network step, alongside
Leaflet and the state boundaries.

### 4.2 Point lookup

`viz/emit.py` reads the footprints on first use into a list of (bounding box, ring, properties)
and keeps it at module level. A query tests the point against each bounding box, then ray-casts
containment for the survivors, and returns the matching properties sorted newest first. Twenty-nine
thousand five-vertex rings test in well under 50 ms with no new dependency. Footprints and the
click are both in longitude and latitude; none crosses the antimeridian.

### 4.3 Server

`GET /api/emit/footprints.geojson` serves the file with the GeoJSON content type, or 404 with a
message naming `./run.sh emit` when absent. `/api/point` gains `emit`, the array from Section 4.2,
empty when the file is absent. `serve` warns on stderr when the file is missing rather than
refusing, since the layer is optional. The catalog route computes `emit_count` and `emit_fetched`
(the file's modification time as an ISO timestamp, or null) from the file on each request, so a
fresh `./run.sh emit` is visible without rerunning `prepare`.

### 4.4 Layer and controls

An "EMIT footprints" checkbox, off by default so the map opens as it does today, disabled with a
note when the catalog reports no footprints. Beneath it two sliders: "Max cloud cover" from 0 to
100%, default 30, and "Window ±days" from 0 to 30, default 7, where 0 disables time filtering.

Footprints draw through Leaflet's Canvas renderer, since the SVG renderer degrades past roughly
ten thousand paths, in a pane between the CPC layer and the state boundaries, as outlines with no
fill at weight 1, coloured by acquisition year through five categorical hues chosen at
implementation to stay distinct from the CDL crop colours and the CPC ramp. Scenes whose cloud
cover exceeds the threshold are not drawn. When the window is active, scenes whose `start` lies
within ±N days of the selected CPC week's Sunday draw in one strong highlight colour at weight 2.5;
all others stay in their faint year colour. A legend block lists the year colours and the
highlight. The footprints are interactive so hovering shows a tooltip with date and cloud cover,
while the map click still opens the readout popup.

### 4.5 Popup

After the CPC series the readout gains a block headed "EMIT scenes covering this point: N",
listing up to fifteen granules nearest in time to the selected CPC week, each with acquisition
date, cloud percentage, browse and data links opening in a new tab, and a mark when inside the
window, followed by "and N more" when truncated. The cloud slider does not apply to this list, so a
scene hidden on the map is still found here.

## 5. Testing

Unit: CMR JSON to GeoJSON conversion against a saved fixture of three entries, including the
latitude-longitude swap and a missing browse link; containment against hand-built polygons with a
point inside, a point inside the bounding box but outside the ring, and a point outside both;
newest-first ordering; the fetch loop's paging with a fake fetcher that returns two pages then an
empty one; the ISO-week-to-Sunday mapping against the 2024 week 15 anchor.

Integration: the point endpoint returns `emit` entries for a fixture footprint containing the
fixture's centre and an empty array elsewhere; the footprints route serves the file and 404s
without it; the catalog carries the two new fields.

Static: the interface references the footprints route, uses `L.canvas`, and carries both sliders.
The browser check is the user's.

## 6. Out of scope

Downloading or rendering EMIT reflectance, per-cell coverage density, and any product other than
L2A reflectance.
