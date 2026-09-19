# USDA CPC over CDL — Local Visualization Design

**Date:** 2026-09-03
**Status:** Implemented on branch `viz-implementation`; see the plan's appendix for post-review amendments

## 1. Purpose

Render the USDA Crop Progress and Condition (CPC) gridded weekly layers on top of the USDA
Cropland Data Layer (CDL) in a local browser, reading every byte from local disk with no network
dependency at view time.

## 2. Source data (verified 2026-09-03)

| Dataset | Extent | Format | Grid | Values |
| --- | --- | --- | --- | --- |
| CPC gridded | 2015–2026 × {corn, cotton, soy, wheat} × {condition, progress} × ~32 weeks = 2,909 rasters (counted) | Float32 GeoTIFF, LZW, zip-within-zip | EPSG:5070, 8999.2555 × 8995.4865 m | Condition ≈ 1.85–4.51; progress 0–1; NoData −9999 |
| CDL 30 m | 2022, 2023, 2024, 2025 | Byte GeoTIFF, PACKBITS, 256-entry palette, embedded RAT, 7 overview levels | EPSG:5070, 30 m; 153811 × 96523 for 2022–2024, 160171 × 105432 for 2025 | Class codes 1–254; 0 = Background |

Extracted sizes, measured rather than estimated: CPC occupies 501 MB and CDL 21 GB, the latter split
7.8 GB each for 2022 and 2023, 2.9 GB for 2024, and 1.8 GB for 2025. The 2022 and 2023 archives carry
far larger overview pyramids than the later years.

Both datasets are native EPSG:5070 (NAD83 / Conus Albers), so they co-register without
reprojection; the only warp required is EPSG:5070 → EPSG:3857 for browser display.

Three properties of the CPC archive constrain the design. Raster extent varies week to week on a
shared grid (317 × 211 at week 15 up to 508 × 320 at week 20), so each file's own geotransform is
read rather than a single assumed extent. The `progress` variable is a monotonic seasonal
development index rather than a named growth stage: corn 2024 rises from a mean of 0.087 at week
15 to 0.983 at week 46, and the legend therefore reads as fraction of season completed. Filenames
change convention mid-archive: 2015–2020 capitalize the crop prefix for corn and soy
(`CornCond15w22.tif`, `SoyCond18w30.tif`) while 2021 onward do not (`cornCond24w15.tif`), so the
parser matches the prefix case-insensitively and normalizes to lowercase. A lowercase-only pattern
silently drops 590 rasters, all of them corn condition, corn progress, or soy condition.

The CDL archives are likewise not uniform. The 2025 archive ships no `.tif.ovr` or `.tfw` sidecar,
embedding its overview pyramid inside the TIFF instead, so extraction writes two files for that year
against four for the others while GDAL still reports seven overview levels.

The 10 m CDL products for 2024 and 2025 are out of scope.

## 3. Environment constraint

The system GDAL 3.8.4 Python bindings are compiled against NumPy 1.x, while NumPy 2.2.4 in the
user site directory shadows the system NumPy 1.21.5 and breaks `osgeo.gdal_array`. Running every
component with `PYTHONNOUSERSITE=1` restores array access. GDAL's own `MEM` and `PNG` drivers cover
tile encoding, and the standard library's `ThreadingHTTPServer` covers serving, so the project
requires no `pip install` and makes no change to the global Python environment.

## 4. Architecture

Four components, each independently testable.

| # | Component | Purpose | Depends on |
| --- | --- | --- | --- |
| 1 | `viz/extract.py` | Idempotent extraction of both archives to a flat layout | `zipfile` |
| 2 | `viz/prepare.py` | Builds `catalog.json` and precomputes 9 km crop-fraction masks | GDAL, NumPy |
| 3 | `viz/tileserver.py` | Warps EPSG:5070 → EPSG:3857 per tile, colorizes, caches to disk | GDAL, NumPy |
| 4 | `viz/web/index.html` | Leaflet interface, four view modes | vendored Leaflet |

### 4.1 Data layout

```
/mnt/c/Users/emgonz38/Downloads/usda/
  usda_crop_progress_and_condition_gridded_layers/   source archives, untouched
  usda_cropland_data_layer/                          source archives, untouched
  data/
    cpc/{crop}/{cond|prog}/{crop}{Cond|Prog}{yy}w{ww}.tif    2,909 files, 501 MB
    cdl/{year}_30m_cdls.tif  + .ovr .aux .tfw                 4 years, 21 GB
    masks/{cdl_year}_{crop}_frac9km.tif                       8 files (2024, 2025), 2 bands each
    catalog.json
  cache/tiles/...                                             grows on demand
  viz/                                                        code
  docs/superpowers/specs/                                     this document
```

## 5. Extraction (`extract.py`)

CPC extraction unwraps two zip levels: the yearly archive holds one directory per crop, each
containing a single inner archive whose `condition/` and `progress/` directories hold the weekly
rasters. Only `.tif` entries are retained; the `.tfw`, `.ovr`, `.aux.xml`, and Esri `.xml` sidecars
are discarded, since the GeoTIFF carries its own geotransform and the fixed color ramps make
precomputed statistics unnecessary. Crop directory names are discovered from each archive rather
than assumed, so a year that departs from the `corn`/`cotton`/`soy`/`wheat` convention still
extracts.

Weekly filenames parse with `(?P<crop>[A-Za-z]+)(?P<var>Cond|Prog)(?P<yy>\d{2})w(?P<ww>\d+)\.tif`,
the crop prefix lowercased before use, and the four-digit year is `2000 + yy`.

CDL extraction copies the `.tif`, `.ovr`, `.aux`, and `.tfw` members of each 30 m archive; the
`.ovr` pyramid is required for tile performance and the `.aux` carries the raster attribute table.

Both paths skip any target already present at the expected size, making re-runs cheap.

## 6. Preparation (`prepare.py`)

### 6.1 Catalog

`catalog.json` records the crops, variables, and the weeks available per crop, variable, and year;
the CDL years present; the CDL years that have a complete set of crop masks; the CDL
code-to-class-name map read from the raster attribute table; the crop code sets in Section 6.2;
and the CPC-year to CDL-year pairing in Section 6.3.

### 6.2 Crop code sets

Each CPC crop maps to a primary set and a double-crop set, kept separate so the interface can
report them independently. A double-crop class counts toward both of its constituent crops.

| Crop | Primary | Double-crop |
| --- | --- | --- |
| corn | 1 | 225, 226, 228, 237, 241 |
| cotton | 2 | 232, 238, 239 |
| soy | 5 | 26, 239, 240, 241, 254 |
| wheat | 22, 23, 24 | 26, 225, 238 |

Sweet corn (12), pop or ornamental corn (13), and buckwheat (39) are excluded, as NASS does not
survey them under the corresponding progress and condition series.

### 6.3 Year pairing

CPC spans 2015–2026 while 30 m CDL spans 2022–2025, so CPC years 2015–2021 pair with CDL 2022 and
CPC 2026 pairs with CDL 2025. The interface labels every substituted pairing explicitly, for
example "CPC 2018 over CDL 2022", so the mismatch is never silent.

### 6.4 Crop-fraction masks

Each mask is a two-band Float32 GeoTIFF on the canonical 508 × 320 CPC grid, with band 1 holding
the primary crop fraction and band 2 the double-crop fraction, both in 0–1.

Masks are built one CDL year at a time. A single VRT wraps the CDL with eight bands over the same
source band, each carrying a `<LUT>` that collapses the 256 CDL codes to 0 or 1 for one crop's
primary or double-crop set, with `ColorInterp` forced to Gray so the palette is not applied. One
`gdal.Warp` call resamples all eight bands to the 9 km grid with `resampleAlg='average'` and
`overviewLevel='NONE'`. Averaging a 0/1 indicator yields exactly the areal fraction of that class
set within each 9 km cell.

Forcing `overviewLevel='NONE'` is deliberate. The `.ovr` pyramid was built for a thematic raster,
so averaging a class indicator sampled from an overview would not give a true areal fraction.
Reading full resolution costs one complete pass over each 2.6 GB CDL year. Bundling all eight
bands into one warp reduces this from sixteen full-resolution passes to four.

A measured probe on 2024 warped a 19226 × 12065 subwindow, one sixty-fourth of the raster, across
all eight bands in 134.8 s reading through `/vsizip`, which projects to roughly 144 minutes per
year and about 10 hours for four. Reading extracted files rather than decompressing the zip on
every access will cut this substantially, by how much is unmeasured. The mask build is therefore
the one genuinely slow step, and it runs one year at a time so the real rate is measured on the
first year before any further year is committed to.

Masks are consequently scoped to CDL 2024 and 2025. All four CDL years are extracted and serve as
base layers; 2022 and 2023 simply have no mask, and the catalog's `mask_years` key records which
years do, so the interface disables the mask control for the rest. Adding those two years later
means running the preparation step with `--year 2022 --year 2023` and requires no code change.

The canonical grid is the maximum CPC extent: 508 × 320, origin (−2309800.2134, 3185470.2868),
pixel size 8999.2555 × −8995.4865. Masks and CPC rasters are never aligned to each other in pixel
space; both are sampled by geographic coordinate at render time, which removes any dependence on
the two grids nesting exactly.

## 7. Tile server (`tileserver.py`)

A `ThreadingHTTPServer` bound to `127.0.0.1:8000`, with the port settable from the command line.

### 7.1 Endpoints

```
GET /                                                   interface
GET /api/catalog                                        contents of catalog.json
GET /tiles/cdl/{year}/{z}/{x}/{y}.png
GET /tiles/cpc/{crop}/{var}/{year}/{week}/{z}/{x}/{y}.png[?mask={cdl_year}]
GET /api/point?lon=&lat=&crop=&year=&cdl_year=          CDL class plus weekly CPC series
```

### 7.2 Tile pipeline

A request converts its `z/x/y` to a Web Mercator envelope, then calls `gdal.Warp` into a 256 × 256
`MEM` dataset bounded by that envelope with `dstSRS='EPSG:3857'`. CDL tiles resample with nearest
neighbour, correct for thematic data, and set `overviewLevel='AUTO'` so GDAL reads the appropriate
pyramid level; this is what keeps a 153811 × 96523 raster responsive at national zoom. CPC tiles
resample bilinearly, appropriate for a continuous field.

Colorization applies a 256-entry lookup table in NumPy. CDL uses the palette read from the source,
with class 0 rendered transparent. Condition uses a diverging red-to-green ramp pinned to the
fixed NASS scale of 1 (very poor) through 3 (fair) to 5 (excellent); progress uses a sequential
ramp pinned to 0–1. Both domains are fixed across every week, crop, and year, so a color carries
the same meaning in every frame and animation stays comparable. NoData renders transparent.

The resulting RGBA array becomes a four-band `MEM` dataset, which `gdal.Translate` writes to PNG
through `/vsimem`.

Rendered tiles are cached to `cache/tiles/{layer-key}/{z}/{x}/{y}.png`, with the mask parameter
part of the layer key. GDAL `Dataset` objects are not safe for concurrent access, so each worker
thread holds its own dataset cache in `threading.local`.

### 7.3 Masked rendering

When `mask={cdl_year}` is present, the mask's two bands warp to the same tile envelope alongside
the CPC values. Alpha becomes `clamp(band1 + band2, 0, 1)` scaled by the opacity set in the
interface, so a 9 km cell that is 60% of the selected crop draws at 60% of that opacity. The
weighting is continuous, with no cutoff threshold.

### 7.4 Point query

The endpoint transforms the clicked longitude and latitude to EPSG:5070, reads the CDL class code
and resolves its name through the attribute table, reads both mask bands to report total crop
cover split into primary and double-crop fractions, and reads that pixel from every weekly CPC
raster for the selected crop and year to return condition and progress series. The weekly rasters
are small enough that reading all 32 costs a single request.

## 8. Interface (`web/index.html`)

Leaflet is vendored into `viz/web/vendor/leaflet/` by a setup step so that viewing requires no
network access.

Controls cover crop, CPC year, week (slider with play control), variable, CDL year, opacity, view
mode, and crop masking.

Overlay and swipe are exclusive view modes. Overlay draws the CPC layer semi-transparently on the
CDL base; swipe is implemented client-side as two Leaflet panes with a draggable clip divider,
requiring no server work. Crop masking per Section 7.3 is an independent toggle that composes with
either mode, giving four valid combinations, and is disabled for any CDL year absent from
`mask_years`. Click-to-inspect is not a mode but an always-live
interaction: clicking anywhere opens a panel showing the CDL class name, the crop-cover split into
primary and double-crop fractions, and an inline SVG sparkline of the weekly condition and progress
series at the clicked cell.

The default view opens on corn condition for 2025 at week 30 over CDL 2025, at CONUS extent, in
transparent-overlay mode at 70% opacity.

A legend shows the active ramp with its fixed domain, and a caption states the active pairing,
including the explicit label when the CDL year is substituted.

## 9. Testing

Tests use the standard library `unittest`, consistent with the no-install constraint.

Unit tests cover the tile `z/x/y` to Web Mercator envelope conversion against known reference
values; lookup table construction for the CDL palette and both CPC ramps, including endpoint and
NoData handling; CPC filename parsing and catalog assembly; the crop code sets, asserting that
double-crop classes appear under both constituent crops and that excluded classes appear under
none; and mask band bounds, asserting fractions stay within 0–1 and that primary and double-crop
bands sum to no more than 1.

An integration test starts the server against a small fixture, requests one CDL tile and one masked
CPC tile, and asserts each response is a valid 256 × 256 PNG.

## 10. Out of scope

The 10 m CDL products, any non-CONUS coverage, reprojection of the source archives, and any
network service at view time.
