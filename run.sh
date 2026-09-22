#!/usr/bin/env bash
# Every entry point runs with PYTHONNOUSERSITE=1 so the system GDAL bindings
# see NumPy 1.21.5 rather than the NumPy 2.x in the user site directory.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONNOUSERSITE=1

case "${1:-}" in
  extract) shift; exec python3 -m viz.extract "$@" ;;
  prepare) shift; exec python3 -m viz.prepare "$@" ;;
  vendor)
    # The only network access in the whole project. Fetches Leaflet 1.9.4 from
    # cdnjs into viz/web/vendor/leaflet/, which is gitignored by design.
    mkdir -p viz/web/vendor/leaflet/images
    curl -fsSL -o viz/web/vendor/leaflet/leaflet.js  https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js
    curl -fsSL -o viz/web/vendor/leaflet/leaflet.css https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css
    for img in marker-icon.png marker-icon-2x.png marker-shadow.png layers.png layers-2x.png; do
      curl -fsSL -o "viz/web/vendor/leaflet/images/$img" "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/$img"
    done
    ls -la viz/web/vendor/leaflet viz/web/vendor/leaflet/images
    # State boundaries: Census cartographic boundary file at 1:5,000,000,
    # converted straight from the zip to CONUS-only GeoJSON. FIPS 02 Alaska,
    # 15 Hawaii, 60 American Samoa, 66 Guam, 69 Northern Marianas,
    # 72 Puerto Rico, 78 Virgin Islands are dropped.
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/states.zip" https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_5m.zip
    python3 -m viz.vendor_states "/vsizip/$tmp/states.zip/cb_2023_us_state_5m.shp" viz/web/vendor/states.geojson
    rm -rf "$tmp"
    ls -la viz/web/vendor/states.geojson
    ;;
  footprints|emit)
    # Fetches EMIT L2A footprints and ECOSTRESS swath boxes over CONUS from
    # NASA's CMR (no login), pairs them, then refreshes the HLS store and its EMIT pairs.
    # Rerun to refresh. Network access. "emit" is kept as an alias.
    shift
    python3 -m viz.fetch_emit "$@"
    python3 -m viz.fetch_eco "$@"
    python3 -m viz.coincidence "$@"
    python3 -m viz.fetch_hls "$@"
    python3 -m viz.hls_pairs
    ;;
  hls)
    # Fetches HLSL30 and HLSS30 v2.0 granule metadata (2022 onward) into
    # data/hls/hls.sqlite, one CSV query per collection-month: about 635 pages
    # in 15-25 minutes on a first run; reruns skip frozen months. Tile outlines
    # are computed from the tile IDs on every run (no ring queries). Then each
    # EMIT scene is paired with its tile's acquisitions within 15 days for the
    # coincidence marking, when EMIT footprints exist. Accepts --from YYYY-MM
    # for a shorter first run. Network access.
    shift
    python3 -m viz.fetch_hls "$@"
    if [ -f data/emit/footprints.geojson ]; then
      python3 -m viz.hls_pairs
    else
      echo "note: no EMIT footprints yet; run ./run.sh footprints to pair them with HLS" >&2
    fi
    ;;
  serve)
    shift
    if [ ! -f viz/web/vendor/leaflet/leaflet.js ] || [ ! -f viz/web/vendor/states.geojson ]; then
      echo "Leaflet or the state boundaries are not vendored; run ./run.sh vendor first" >&2
      exit 1
    fi
    if [ ! -f data/emit/footprints.geojson ]; then
      echo "note: no EMIT footprints (data/emit/footprints.geojson); the EMIT layer is off until ./run.sh footprints is run" >&2
    fi
    if [ ! -f data/eco/footprints.geojson ]; then
      echo "note: no ECOSTRESS footprints (data/eco/footprints.geojson); the ECOSTRESS layer is off until ./run.sh footprints is run" >&2
    fi
    if [ ! -f data/hls/hls.sqlite ]; then
      echo "note: no HLS store (data/hls/hls.sqlite); the HLS layer is off until ./run.sh hls is run" >&2
    fi
    exec python3 -m viz.tileserver "$@"
    ;;
  # -t . keeps the repo root as the top-level import dir so `from viz import ...`
  # and `from tests import fixtures` both resolve.
  test)    shift; exec python3 -m unittest discover -s tests -t . -v "$@" ;;
  *) echo "usage: $0 {extract|prepare|vendor|footprints|emit|hls|serve|test} [args]" >&2; exit 2 ;;
esac
