# Mission Registry — Design

**Date:** 2026-09-30
**Status:** Approved design, pending implementation plan
**Supersedes in part:** `2026-09-21-emit-footprints-design.md`, `2026-09-21-ecostress-coincidence-design.md`,
`2026-09-21-hls-coverage-design.md` (their features remain; their per-mission code paths are replaced)

## 1. Purpose

Turn the three hand-built imagery layers (EMIT, ECOSTRESS, HLS) into instances of one mission
registry, so that further datasets arrive as configuration plus a refresh run rather than as new
fetch scripts, files, and interface blocks. The registry is the first of four projects toward a
published tool for plant scientists, Earth scientists, and mission planners: (1) this registry,
(2) the release-one missions (Landsat Collection 2 Level 2, OPERA RTC-S1, VIIRS land surface
temperature and snow cover), (3) named regions with the crop layers packaged as a region pack,
and (4) publishing (self-host documentation, a reverse proxy in front of the standard-library
server, a hosted instance with refresh jobs). Decisions already taken for the later projects and
binding here: the catalog is scoped by named region, not global; the server stays standard
library plus GDAL behind a reverse proxy; the first public release is CONUS with the crop layers
on.

This project changes nothing a user sees. EMIT, ECOSTRESS, and HLS look and behave as they do
today at the end of every step; the coincidence controls gain generality (§6) and the sidebar's
imagery group becomes generated (§7).

## 2. Registry (`viz/missions.json`)

One JSON file in the repository, loaded and validated once at server start and by every
vendoring or refresh command. It holds:

- `region`: `{"name": "CONUS", "bbox": [-125.0, 24.4, -66.9, 49.4]}`, the one region for this
  project, replacing the box repeated in the basins, rivers, and HLS code. Project 3 makes it a
  list.
- `missions`: a list of entries.

Fields common to both archetypes:

| Field | Meaning |
| --- | --- |
| `key` | short id used in URLs, element ids, and the catalog (`emit`, `eco`, `hls`) |
| `name`, `label` | display name and the checkbox text |
| `archetype` | `swath` or `tiled` |
| `cmr` | list of collections, each `short_name`, `version`, and an optional `implies` map of constant attributes the collection sets (HLS: `{"sensor": "L30"}` on one collection, `S30` on the other) |
| `since` | first month to catalog, `YYYY-MM` |
| `attributes` | named values from the CMR entry: `{"cloud": {"from": "cloud_cover", "type": "number"}, "daynight": {"from": "day_night_flag", "type": "text"}, "orbit": {"from": "orbit", "type": "int"}}`; `from` names a CMR entry field or one of the archetype's derived fields (`orbit` from the orbit domain, `sensor` from `implies`) |
| `filters` | the controls the sidebar generates, each bound to an attribute: `{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud cover"}` or `{"attribute": "daynight", "control": "choice", "values": ["DAY", "NIGHT", "ALL"], "default": "DAY"}`; `ALL` means no filter |
| `browse` | `{"source": "links", "match": "\\.png$"}` or `{"source": "sibling", "short_name": …, "version": …, "id_pattern": …, "sibling_pattern": …, "kinds": {…}}` (§8) |
| `links` | other links to show, `{"data": {"rel": "data#", "match": "\\.nc$"}}` |
| `style` | `{"colour_by": "year", "colours": {...}}` or `{"colour": "#5b6770"}`, plus `pane` order |

Swath entries add `footprint`: `polygon` (the CMR `polygons` ring) or `box` (the CMR `boxes`
rectangle). Tiled entries add `grid` (`mgrs` in this project; `wrs2` and `sinusoidal` in
project 2) and `tile_from`: `{"field": "title", "pattern": "^HLS\\.[LS]30\\.T([0-9]{2}[A-Z]{3})\\."}`.

The archetype decides what the file does not say: tiled missions fetch CSV pages by month (they
need id, time, cloud, and browse only), swath missions fetch JSON pages (they need the geometry);
storage and drawing are the archetype's.

Validation rejects duplicate keys, an unknown archetype, grid, footprint, control, or browse
source, a pattern that does not compile, a filter bound to an undeclared attribute, and a
`since` that is not a month, each with the mission key in the message. The server refuses to
start on an invalid registry.

The three initial entries, abbreviated (the plan carries them in full):

```json
{"key": "emit", "name": "EMIT", "label": "EMIT footprints", "archetype": "swath", "footprint": "polygon",
 "cmr": [{"short_name": "EMITL2ARFL", "version": "001"}], "since": "2022-08",
 "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}},
 "filters": [{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud cover"}],
 "browse": {"source": "links", "match": "\\.png$"}, "links": {"data": {"rel": "data#", "match": "\\.nc$"}},
 "style": {"colour_by": "year"}}

{"key": "eco", "name": "ECOSTRESS", "label": "ECOSTRESS swaths", "archetype": "swath", "footprint": "box",
 "cmr": [{"short_name": "ECO_L2_LSTE", "version": "002"}], "since": "2022-01",
 "attributes": {"daynight": {"from": "day_night_flag", "type": "text"}, "orbit": {"from": "orbit", "type": "int"}},
 "filters": [{"attribute": "daynight", "control": "choice", "values": ["DAY", "NIGHT", "ALL"], "default": "DAY"}],
 "browse": {"source": "sibling", "short_name": "ECO_L2T_LSTE", "version": "002",
            "id_pattern": "^ECOv002_L2_LSTE_(\\d+)_(\\d+)_", "sibling_pattern": "ECOv002_L2T_LSTE_{0}_{1}_*",
            "kinds": {"lst": "_LST.jpeg", "qc": "_QC.jpeg", "cloud": "_cloud.jpeg"}},
 "style": {"colour": "#5b6770"}}

{"key": "hls", "name": "HLS", "label": "HLS coverage", "archetype": "tiled", "grid": "mgrs",
 "cmr": [{"short_name": "HLSL30", "version": "2.0", "implies": {"sensor": "L30"}},
         {"short_name": "HLSS30", "version": "2.0", "implies": {"sensor": "S30"}}],
 "since": "2022-01", "tile_from": {"field": "title", "pattern": "^HLS\\.[LS]30\\.T([0-9]{2}[A-Z]{3})\\."},
 "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}, "sensor": {"from": "sensor", "type": "text"}},
 "filters": [{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud"},
             {"attribute": "sensor", "control": "choice", "values": ["ALL", "L30", "S30"], "default": "ALL"}],
 "browse": {"source": "links", "match": "\\.jpg$"}}
```

## 3. Catalog

One SQLite file per region, `data/catalog/<region>.sqlite` (git-ignored), opened read-only by
the server (`file:...?mode=ro`, 0.5 s timeout, one connection per thread re-opened when the file's
modification time changes). A locked catalog reports busy: catalog routes answer 503 and the point
readout's mission blocks are null, as the HLS store behaves today; any other fault is a 500.

| Table | Columns | Indexes |
| --- | --- | --- |
| `granules` | `mission TEXT`, `id TEXT`, `start TEXT`, `end TEXT`, `cloud REAL`, `daynight TEXT`, `sensor TEXT`, `orbit INTEGER`, `tile TEXT`, `minlon REAL`, `minlat REAL`, `maxlon REAL`, `maxlat REAL`, `ring TEXT` (JSON), `browse TEXT`, `data TEXT`, `attrs TEXT` (JSON); primary key `(mission, id)` | `(mission, start)`, `(mission, tile, start, cloud)`, `(mission, minlat, maxlat, start)` |
| `tiles` | `grid TEXT`, `tile TEXT`, `ring TEXT` (JSON, closed); primary key `(grid, tile)` | |
| `coverage` | `mission TEXT`, `id TEXT`, `grid TEXT`, `tile TEXT`; primary key `(mission, id, grid, tile)` | `(grid, tile, mission)` |
| `months` | `mission TEXT`, `month TEXT`, `count INTEGER`, `fetched_at TEXT`; primary key `(mission, month)` | |

Typed attribute columns hold the attributes the three missions and the release-one missions
declare; an attribute the registry declares under another name is stored in `attrs`. `start` and
`end` are ISO UTC; dates compare as text. Swath rows carry the box and ring; tiled rows carry
`tile`. `coverage` holds, for every swath granule, the tiles of every grid in use whose ring its
box intersects and whose ring the granule's ring intersects (bounding-box prefilter, then the ring
test), written at refresh.

### 3.1 Refresh

`./run.sh refresh [MISSION ...]` (all missions by default) walks the registry. For each mission
and each month from `since` to the current month: skip a frozen month (its `months` row was
written sixty days or more after the month ended); page CMR for each of the mission's collections
(CSV for tiled with the `CMR-Hits` header check and the under-delivery error; JSON for swath, with
the `page_num × page_size ≤ 1,000,000` split by month as now); replace the month in one
transaction (delete the mission's rows whose `start` falls in the month, insert, upsert `months`).
After the pass, compute rings for tile ids not yet in `tiles` and `coverage` rows for swath
granules not yet covered. Progress is printed per mission and month; a failed month leaves the
previous month's rows in place.

`./run.sh migrate` imports the existing stores once: `data/hls/hls.sqlite` (rows and tile rings),
`data/emit/footprints.geojson`, and `data/eco/footprints.geojson`, into the catalog, stamping the
footprint files' months with the file's modification time and the HLS months with the earliest
sensor fetch time, so a month whose data may be incomplete is refetched. The old files
are left in place until the sweep step deletes their readers.

The commands `hls`, `footprints`, and `emit` in `run.sh` become aliases of `refresh` during the
migration and are removed in the sweep.

## 4. Archetypes

Each archetype is a module under `viz/archetypes/` with five duties, called by the server and the
refresh command without regard to which mission they hold:

| Duty | Swath | Tiled |
| --- | --- | --- |
| Fetch a month | JSON pages; ring from `polygons` or `boxes` per `footprint`; attributes by the registry map | CSV pages with the hits check; tile id by `tile_from`; attributes from the CSV columns and `implies` |
| Store | `granules` with box and ring | `granules` with `tile`; new tile ids get rings from the grid module |
| Serve geometry | `footprints.geojson` for the mission, cached against the catalog file | `tiles.geojson` for the grid, cached; `counts` over a range and filters |
| Point lookup | granules whose ring contains the point, from a spatial index over the rings, cached with the catalog | tiles containing the point from the same index over tile rings, then acquisitions in those tiles over the range |
| Browse | the registry rule (§8) | a link on the granule |

Grids live under `viz/grids/`, one module each exposing `ring(tile_id)`; `mgrs` is the HLS
tile-ring code moved unchanged. Shared modules: `viz/registry.py` (load, validate, missions by
key), `viz/catalog.py` (schema, connections, month replacement, shared queries), `viz/spatial.py`
(the point-in-ring index from `viz/emit.py`, used by both archetypes and the basins).

Retired as each mission migrates: `fetch_emit.py`, `fetch_eco.py`, `fetch_hls.py`, `hls_pairs.py`,
`coincidence.py`, `eco_browse.py`, `emit.py`, `hls.py`, with their tests replaced by archetype
tests on fixtures and a migration test.

## 5. Routes

| Route | Answer |
| --- | --- |
| `GET /api/catalog` | as now, plus `missions`: for each registry mission its key, name, label, archetype, filters, style, granule count, and last refresh; and `catalog_busy` |
| `GET /api/missions/<key>/footprints.geojson` | swath: FeatureCollection with the mission's granules and their attributes; 404 for a tiled mission or an unknown key |
| `GET /api/missions/<key>/tiles.geojson` | tiled: the grid's tiles with a `tile` property |
| `GET /api/missions/<key>/counts?start&end&<filters>` | tiled: `{"counts": {tile: n}}`, zero tiles omitted; 400 on a bad date or filter value |
| `GET /api/missions/<key>/browse?id&lon&lat` | §8 |
| `GET /api/coincidence?…` | §6 |
| `GET /api/point?…` | as now, with one block per mission that is on (`missions` query parameter, comma-separated keys): swath blocks list covering granules with attributes and links; tiled blocks list acquisitions per covering tile with the clear count; both null when the catalog is busy |

Filter values are passed as `f.<attribute>=<value>` on the per-mission routes and as
`f.<mission>.<attribute>=<value>` on the coincidence route, and validated against the registry (a
`max` control takes a number, a `choice` control one of its values; an unknown name is a 400).

## 6. Coincidence

One panel in the imagery group: a subject (any swath mission that is on), partner checkboxes
(every other mission that is on), a same-pass window in minutes for swath partners, a revisit
window in days for tiled partners, and "Only coincident scenes". The default configuration is
today's: subject EMIT, partners ECOSTRESS and HLS, 15 minutes, 7 days.

`GET /api/coincidence?subject=emit&partners=eco,hls&minutes=15&days=7&start=…&end=…&f.eco.daynight=DAY&f.hls.cloud=30&f.hls.sensor=ALL`
answers `{"marked": {"<subject id>": {"eco": -312, "hls": 2}}}` for the subject granules in the
range that have every chosen partner inside its window, with the closest offset per partner
(signed seconds for swath partners, signed whole days for tiled ones). Rules, in SQL:

- swath partner: `|start_subject − start_partner| ≤ minutes` and the bounding boxes intersect;
- tiled partner: a partner granule in a tile the subject covers (`coverage`) whose date lies
  within `days` of the subject's date and which passes the partner's filters.

The date range is the one the map shows, so responses stay in the hundreds of ids. The client
marks those features and derives the per-scene tags in the readout from the same answer. A
tiled mission cannot be the subject in this project; tiled-to-tiled questions are a later addition
on the same route. 400 on a subject that is not a swath mission, an unknown partner, or a bad
window or date.

## 7. Interface

The imagery group is generated at startup from the catalog's `missions` list: per mission a
checkbox (`id="mission-<key>"`), a sub-controls block from its filters (`max` slider with output,
`choice` radio row), and the archetype's extras (tiled: week-or-season mode and the five-class
count legend; swath: the colour legend the style implies). The time-window slider stays at the
top of the group, the coincidence panel at the bottom. A mission with no granules is disabled with
a note naming `./run.sh refresh`.

Two generic layers replace the three bespoke ones: a swath layer (canvas GeoJSON, style from the
registry, in-window highlight, coincidence marks, "only in window" and "only coincident" filters)
and a tiled layer (outlines recoloured from `counts`, hover tooltip with the count). Both read
their filter values from the generated controls by attribute name.

The readout gains one collapsible section per mission that is on, generated the same way: swath
sections list the granules covering the point sorted by closeness to the selected week with the
declared attributes, a browse link, extra links, and the coincidence tags; tiled sections list
acquisitions per covering tile with the clear count. The crop readout and the basin line are
untouched.

`app.js` keeps the map, crop layers, readout shell, and wiring; the generic code lives in sibling
scripts loaded by plain script tags in order: `missions.js` (control generation and the two
layer types), `coincidence.js`, `readout.js` (the readout code moved). No build step.

## 8. Browse and the viewer

`GET /api/missions/<key>/browse?id&lon&lat`: for a `links` mission, the granule's stored browse
link and declared extra links from the catalog, no network; for a `sibling` mission, the
`eco_browse` lookup generalized to the registry's sibling collection, id pattern, sibling name
pattern, and picture kinds, with the same cache and the same statuses (400 bad request, 404 no
picture covers the point, 502 upstream failure). The viewer is shared; its caption is built from
the mission name, the date and time, and the attributes the registry lists.

## 9. Migration sequence

One branch and pull request per step; the full suite green and the tool working at every merge:

1. **Foundations.** `missions.json` with the three entries, `registry.py`, `catalog.py` with the
   schema and busy handling, `spatial.py`, the `migrate` command, and `refresh` able to run any
   mission into the catalog. Nothing on screen changes; the old paths still serve.
2. **HLS onto the tiled archetype.** Counts, tiles, readout section, and layer served from the
   catalog through the generic routes and the generated controls; `hls.py`, `fetch_hls.py`, and
   the HLS block retire.
3. **ECOSTRESS onto the swath archetype**, including the sibling browse rule; `fetch_eco.py`,
   `eco_browse.py`, and its block retire.
4. **EMIT onto the swath archetype**, the coincidence panel and route replacing the baked
   pairing, the generated imagery group replacing the last hand-written block; `fetch_emit.py`,
   `hls_pairs.py`, `coincidence.py`, and `emit.py` retire.
5. **Sweep.** The region box read from the registry in the basins, rivers, and any remaining
   code; `run.sh` reduced to `prepare`, `vendor`, `refresh`, `migrate`, `serve`, `test`; a page in
   `docs/` on adding a mission.

## 10. Testing

- Registry: the real file validates; fixtures with a duplicate key, an unknown archetype, a bad
  pattern, and a filter on an undeclared attribute fail naming the mission.
- Catalog: schema creation; replacing a month twice gives the same rows; the frozen rule at 59,
  60, and 61 days; queries under each filter and at a cloud boundary; the busy path with a writer
  holding the lock; `migrate` on a small HLS store and two footprint fixtures gives the expected
  granule, tile, and coverage counts.
- Archetypes: swath with a fake fetcher serving a polygon entry and a box entry (attribute map,
  ring and box storage, footprints, point lookup, coverage against a fixture grid); tiled with CSV
  pages (hits check, under-delivery error, tile parsing, counts, acquisitions, tiles, point
  lookup); `mgrs` keeps its tests.
- Coincidence: partners inside and outside the minute window, boxes that do and do not
  intersect, a tiled partner in a covered tile inside and outside the day window, a partner
  removed by its filter; parameter validation; response shape.
- Routes: each generic route on a fixture catalog, browse for a `links` mission with no fetcher
  and a `sibling` mission with a faked one, the catalog's `missions` list, the point blocks, and the
  404 and 503 paths.
- Interface: static asserts on the generator, the id scheme, the coincidence panel, and the three
  sibling scripts in order; a node runtime check builds the controls from a fixture missions list
  and checks ids and defaults.
- Migration proof, run once on the real data and recorded in the plan's appendix: old against new
  HLS counts after step 2, the baked pairing against the coincidence query after step 4.

## 11. Out of scope

New missions (project 2), multiple regions and the crop region pack (project 3), the reverse
proxy and hosting (project 4), tiled subjects in coincidence, per-pixel data rendering from
protected files, and any change to the crop layers or the basin and river layers beyond reading
the region box from the registry.
