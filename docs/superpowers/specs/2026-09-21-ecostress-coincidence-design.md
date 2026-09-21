# ECOSTRESS Footprints and EMIT–ECOSTRESS Coincidence — Design

**Date:** 2026-09-21
**Status:** Approved design, pending implementation plan
**Extends:** `2026-09-21-emit-footprints-design.md`

## 1. Purpose

Add ECOSTRESS scene footprints beside the EMIT layer, and identify where and when the two
instruments collected data together, so that a low crop-condition cell can be checked for a
same-pass pair of hyperspectral reflectance (EMIT) and thermal (ECOSTRESS) observations. The
combination is the basis of a planned research project on crop stress; this feature answers "is
there a joint acquisition here, when, and how far apart in time" and stops short of data download.

## 2. Source data (verified 2026-09-21)

ECOSTRESS (ECOsystem Spaceborne Thermal Radiometer Experiment on Space Station) is a thermal
imager on the ISS with a swath about 400 km wide at 70 m. Its scene-level product with one granule
per acquisition is the swath land surface temperature and emissivity, `ECO_L2_LSTE` version 002,
at the LP DAAC. The tiled products (`ECO_L2T_*`, `ECO_L3T_*`, `ECO_L4T_*`) split each scene into
110 km MGRS tiles and hold 2.7 million granules over CONUS, too many to serve as a layer.

In CMR, swath granules carry a bounding box (`boxes`), start and end times, an orbit number, and a
`day_night_flag`. They carry **no footprint polygon and no cloud cover**. The boxes are coarse: for
a 52-second scene the box is about 6° × 5°, roughly 550 km on a side against a real swath of about
400 km. Counts over the CONUS bounding box:

| Year | Swath granules |
| --- | --- |
| 2018 | 2,997 |
| 2019 | 11,477 |
| 2020 | 13,887 |
| 2021 | 15,264 |
| 2022 | 17,845 |
| 2023 | 24,751 |
| 2024 | 11,305 |
| 2025 | 12,880 |
| 2026 to date | 8,949 |
| Total | 119,355 |

Of a July 2025 sample of 200, 144 were night passes; thermal imaging does not need sunlight, and
night scenes can never coincide with EMIT, whose reflectance product needs the sun.

### 2.1 Coincidence, measured

Both instruments ride the ISS, so a same-pass pair is the expected case. Sampling 120 EMIT scenes
over the Corn Belt in the 2025 growing season and searching CMR for an ECOSTRESS swath whose box
overlaps the EMIT footprint:

| Window | EMIT scenes with an overlapping ECOSTRESS swath |
| --- | --- |
| ±15 min | 100 of 120 (83%) |
| ±2 h | 100 of 120 (83%) |
| ±1 day | 118 of 120 (98%) |
| ±7 days | 120 of 120 (100%) |

Same-pass pairs are seconds apart (11 s, 41 s, 67 s in the sampled examples). Because EMIT's 75 km
swath lies inside ECOSTRESS's 400 km swath on a shared pass, the footprint of a joint acquisition
is, to the precision this tool needs, the EMIT footprint. That is what makes a precise coincidence
layer possible even though ECOSTRESS's own footprints are only boxes.

## 3. Decisions

Recorded from the design conversation:

- ECOSTRESS footprints are drawn as their CMR boxes, dashed and explicitly captioned as bounding
  boxes of about 550 km, not swath outlines.
- "Collected together" means an ECOSTRESS swath box overlapping the EMIT footprint within a window
  that defaults to 15 minutes and is adjustable from 1 minute to 24 hours. Pairs are precomputed
  at fetch time up to ±24 h so the slider only filters.
- Day and night swaths are both fetched, with a Day / Night / Both control defaulting to Day.
- ECOSTRESS is fetched from 2022 onward, matching EMIT's span, about 75,000 swaths.

## 4. Architecture

CMR paging and atomic writing move into `viz/cmr.py`, shared by both fetch scripts. Coincidence is
computed once at fetch time by `viz/coincidence.py` and written onto the EMIT features, so the
browser only filters. The alternative, intersecting 29 thousand polygons against 75 thousand boxes
in the browser on every slider move, would make the control sluggish for no gain.

| Component | Purpose |
| --- | --- |
| `viz/cmr.py` | Paged granule search, entry helpers, atomic GeoJSON write |
| `viz/fetch_emit.py` | EMIT fetch, now through `cmr.py` |
| `viz/fetch_eco.py` | ECOSTRESS swath fetch into `data/eco/footprints.geojson` |
| `viz/coincidence.py` | Time-sorted sweep pairing EMIT scenes with overlapping ECOSTRESS swaths |
| `viz/emit.py` | `FootprintIndex` unchanged; serves both files by path |
| `viz/tileserver.py` | ECOSTRESS route; `eco` on `/api/point`; catalog fields |
| `viz/web/` | ECOSTRESS layer and controls, coincidence controls and styling, popup additions |

### 4.1 Fetch

`./run.sh footprints` runs, in order: the EMIT fetch as today; the ECOSTRESS fetch, requesting
`short_name=ECO_L2_LSTE&version=002` over the CONUS box with `temporal=2022-01-01T00:00:00Z,` open
ended, paging by 2,000; then coincidence. `./run.sh emit` remains as an alias for the whole
sequence so existing instructions keep working.

Each ECOSTRESS entry becomes one rectangle polygon from its first box (CMR order is south, west,
north, east) with properties `id` (the title), `start`, `end`, `daynight` (`DAY` or `NIGHT`),
`year`, and `orbit` (from `orbit_calculated_spatial_domains`). Entries without a box are skipped.
`browse` and `cloud` are absent by design; the interface must not assume them.

### 4.2 Coincidence

`viz/coincidence.py` loads both files, sorts ECOSTRESS swaths by start time, and for each EMIT
scene binary-searches the ±24 h neighbourhood, keeps swaths whose box intersects the EMIT
footprint's bounding box, and records up to 20 as the EMIT feature's `eco` property, nearest in
time first: each `{id, start, daynight, dt}` where `dt` is ECOSTRESS start minus EMIT start in
signed seconds. Box-against-box intersection is the right test here: the joint footprint is the
EMIT scene, and the ECOSTRESS box is already an over-estimate of the swath. The EMIT file is
rewritten atomically with the new property. A summary line reports how many EMIT scenes have a
pair within 15 minutes; the same number is exposed as the catalog's `coincident_15min`.

### 4.3 Server

`GET /api/eco/footprints.geojson` mirrors the EMIT route. `/api/point` gains `eco`: every
ECOSTRESS swath whose box contains the point, newest first. EMIT entries returned by the same call
now carry `eco` pairs. The catalog gains `eco_count`, `eco_fetched`, and `coincident_15min`,
computed from the files at request time like the EMIT fields. `serve` warns, not refuses, when the
ECOSTRESS file is absent.

### 4.4 ECOSTRESS layer

A checkbox "ECOSTRESS swaths", off by default, disabled with a note when the catalog reports none;
beneath it a Day / Night / Both radio defaulting to Day. Boxes draw through the Canvas renderer in
a pane below EMIT, as dashed slate-grey rectangles (`#5b6770`, weight 1, dash `4 4`), one colour
with no year coding. The legend entry reads "ECOSTRESS swath, bounding box (≈550 km), not the true
outline". Hovering shows date, time, and day or night. The existing CPC-week window applies to
this layer too, with the same black highlight and fade, so a week's ECOSTRESS passes stand out.

### 4.5 Coincidence controls

Under the EMIT controls: a checkbox "Mark ECOSTRESS coincidence", a slider "Coincidence window"
stepping through 1, 5, 15, 30 minutes and 1, 2, 6, 12, 24 hours with 15 minutes as the default,
and a checkbox "Only coincident scenes". When marking is on, an EMIT footprint whose nearest `eco`
pair has `|dt|` within the window gains a translucent fill in its year colour at 25% opacity;
footprints without one keep their outline only. Fill is independent of the black CPC-week outline,
so the two states combine legibly: a black-outlined, filled footprint is near the selected week
and was observed by ECOSTRESS on the same pass. "Only coincident scenes" hides the unpaired
footprints, mirroring "Only scenes within window". Both coincidence controls are active only while
the EMIT layer is on and the catalog reports ECOSTRESS data.

### 4.6 Popup

Each EMIT row gains a tag like "ECOSTRESS +41 s" (or "−2 h 13 m" for larger offsets) when its
nearest pair falls inside the coincidence window. A new block, "ECOSTRESS swaths covering this
point: N", lists up to ten nearest in time to the selected CPC week with date, time, and day or
night, then "and N more" when truncated.

## 5. Testing

Unit: box string to rectangle ring in longitude-latitude order; ECOSTRESS entry to feature with
and without a box; the sweep against hand-built scenes with pairs at 11 s, 3 h, and 30 h,
asserting the 30 h pair is excluded, the order is nearest first, a non-overlapping box is excluded,
and `dt` carries sign; the 20-pair cap; the shared CMR pager against a fake fetcher.

Integration: the ECOSTRESS route serves the fixture and 404s without it; `/api/point` returns
`eco` for a fixture box containing the point and an empty list elsewhere; the catalog carries the
three new fields; EMIT entries include `eco` pairs.

Static: the interface references the ECOSTRESS route, carries the Day / Night / Both control, both
coincidence controls, and the slider steps. The browser check is the user's.

## 6. Out of scope

True ECOSTRESS swath outlines, cloud cover for ECOSTRESS, the tiled products, coincidence with any
third mission, and downloading or rendering any EMIT or ECOSTRESS data product.
