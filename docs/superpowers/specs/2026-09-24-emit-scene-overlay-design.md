# EMIT Scene Overlay — Design

**Date:** 2026-09-24
**Status:** Approved design, pending implementation plan
**Extends:** `2026-09-21-emit-footprints-design.md`

## 1. Purpose

Show an EMIT scene's browse image on the map itself, georeferenced inside its footprint, when a
scene is chosen from the click readout, instead of opening the image in a new browser tab. The
overlay lets a low crop-condition cell be compared at once with what the spectrometer saw, at the
quicklook's resolution, without leaving the map or downloading the reflectance file.

## 2. Source data (verified 2026-09-24)

Every EMIT L2A granule in CMR carries a browse link to a PNG quicklook on the LP DAAC public
bucket (`https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-public/EMITL2ARFL.001/<id>/<id>.png`),
already stored on each footprint as its `browse` property. The image needs no login, is about
1.4 MB, and measures 1242 by 1280 pixels: 1242 cross-track samples by 1280 along-track lines at
roughly 60 m, in the sensor's own swath geometry rather than north-up. Its four corners are the
four vertices of the footprint ring.

The ring is always counter-clockwise in image space, but which vertex is the image's top-left
depends on the direction of the pass. The direction comes from the footprint file itself: scene
numbers within one orbit (`…_2421214_004`, `…_2421214_005`) are consecutive along the track, so
the vector from a scene's centroid to its neighbour's gives the flight direction. Rows increase
along the flight direction, so the two vertices behind the centroid form the top edge, and the
image's columns increase toward the left of the flight direction, so the top-left corner is the
vertex to the right. This rule reproduces the two scenes checked visually against the state
shoreline, an ascending pass over San Francisco Bay and a descending one over the Miami coast.
Of 28,878 scenes, 28,458 have a same-orbit neighbour; the other 420 cannot be oriented and keep
the browse link only.

The footprint quad is a parallelogram to within a few tens of metres, so a first-order polynomial
warp from four ground control points places the image without residuals.

## 3. Architecture

The server renders the overlay as ordinary Web Mercator tiles through the same per-tile warp and
disk cache that serve the CDL and CPC layers; the browser adds one more tile layer. The only new
element is that the server fetches a browse image from the network on demand, once per scene, and
keeps it under `cache/`; this is the project's first on-demand network access and is confined to
the LP DAAC host recorded on the footprint.

| Component | Purpose |
| --- | --- |
| `viz/emit.py` | Orbit neighbour lookup and the corner rule: `scene_corners(index, scene_id)` |
| `viz/scene.py` | Browse download into `cache/emit/<id>.png`; the GCP virtual raster; per-tile warp with alpha |
| `viz/tileserver.py` | `/tiles/emit/<id>/{z}/{x}/{y}.png` |
| `viz/paths.py` | `SCENE_CACHE = CACHE / "emit"` |
| `viz/web/` | "show" action in the readout, the scene pane and layer, the "Scene on map" control |

### 3.1 Orientation

`FootprintIndex` gains a map from `(orbit, scene number)` to properties, parsed from the granule
ID's last two underscore fields (`2421214` and `004`), and `scene_corners(index, scene_id)`
returns the ring vertices in image order `[top-left, top-right, bottom-right, bottom-left]`, or
`None` when the ID is malformed or the scene has neither the next nor the previous number in its
orbit. The flight direction is the unit vector from the scene centroid to the next scene's
centroid (or from the previous scene's to this one), in a local frame with longitudes scaled by
the cosine of the latitude. Each vertex gets an along-track and a left-of-track coordinate; the two
smallest along-track values are the top edge; within each edge the vertex with the smaller
left-of-track value is the left one in the image.

### 3.2 Browse download and warp

`viz/scene.py` exposes `browse_path(scene_id) -> Path` (the cache file), `ensure_browse(props)`
which downloads the footprint's `browse` URL into `cache/emit/<id>.png` through a `.part` file
and an atomic rename when absent, refusing any URL whose host is not
`data.lpdaac.earthdatacloud.nasa.gov`, and `render_tile(scene_id, props, corners, z, x, y)`. The
render builds, once per scene per process, an in-memory GDAL virtual raster from the cached PNG
with four ground control points mapping pixel corners `(0, 0)`, `(w, 0)`, `(w, h)`, `(0, h)` to the
four ordered vertices in EPSG:4326, then warps the tile to EPSG:3857 at 256 by 256 with bilinear
resampling, a first-order polynomial transform, and a destination alpha band, so pixels outside
the quad are transparent. The result goes through the existing PNG encoder and tile cache under
the key `emit-<id>`, and a request for a tile that does not touch the scene's bounding box is
answered from a shared fully transparent tile without a warp.

### 3.3 Server

`GET /tiles/emit/<id>/{z}/{x}/{y}.png` looks the scene up in the footprint index (404 for an
unknown ID), resolves its corners (404 with the message "scene cannot be oriented" when there is
no orbit neighbour), ensures the browse file (502 with the message "browse image unavailable" if
the download fails, so the browser shows nothing rather than a broken tile), and serves the tile
with the same cache headers as the other tile routes. Downloads are serialised per scene with a
lock so concurrent tile requests for a new scene fetch the image once. The catalog is unchanged.

### 3.4 Interface

Each EMIT row in the readout gains a "show" action before the existing "browse" and "data"
links; rows for scenes without a neighbour omit it. Choosing it adds a tile layer for the scene
(`/tiles/emit/<id>/{z}/{x}/{y}.png?t=<server token>`, `maxNativeZoom` 13, no wrap) in a new
pane `scene` at z-index 453, between the ECOSTRESS swaths (452) and the EMIT outlines (455), and
zooms the map to the scene's footprint if the footprint is not already in view. One scene shows
at a time: choosing another replaces the layer. A "Scene on map" line in the EMIT block names the
scene's date and ID, carries an opacity slider (0–100, default 100) and a remove button, and
stays hidden while no scene is shown. The popup's per-scene tags and lists are unchanged.

## 4. Testing

Unit: the orbit-key parsing and neighbour lookup on a three-scene fixture; the corner rule on the
two verified scenes' rings and neighbours (expected orders `[2, 1, 0, 3]` for the ascending San
Francisco pass and `[1, 0, 3, 2]` for the descending Miami pass); `None` for a malformed ID and
for a scene without a neighbour; the host check on `ensure_browse`; the warp on a synthetic
4-by-4 PNG with one coloured corner pixel, checking that the colour lands in the tile at the
vertex it was mapped to and that outside pixels are transparent.

Integration: the tile route serves a 256-by-256 PNG for a fixture scene whose browse download is
replaced by a fake that writes the synthetic PNG into the cache; 404 for an unknown ID and for
the fixture scene without a neighbour; 502 when the fake download raises; a second request is
served from the disk cache without calling the fake again.

Static: the interface references the scene tile route, the `scene` pane at 453, the "Scene on
map" control, and the opacity slider. The browser check is the user's.

## 5. Out of scope

Downloading or rendering the reflectance file, showing more than one scene at a time, band
selection or stretch of the quicklook, and orienting the 420 scenes that have no orbit neighbour.
