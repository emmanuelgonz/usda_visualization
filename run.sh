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
  emit)
    # Fetches every EMIT L2A footprint over CONUS from NASA's CMR (no login)
    # into data/emit/footprints.geojson. Rerun to refresh. Network access.
    shift; exec python3 -m viz.fetch_emit "$@" ;;
  serve)
    shift
    if [ ! -f viz/web/vendor/leaflet/leaflet.js ] || [ ! -f viz/web/vendor/states.geojson ]; then
      echo "Leaflet or the state boundaries are not vendored; run ./run.sh vendor first" >&2
      exit 1
    fi
    if [ ! -f data/emit/footprints.geojson ]; then
      echo "note: no EMIT footprints (data/emit/footprints.geojson); the EMIT layer is off until ./run.sh emit is run" >&2
    fi
    exec python3 -m viz.tileserver "$@"
    ;;
  # -t . keeps the repo root as the top-level import dir so `from viz import ...`
  # and `from tests import fixtures` both resolve.
  test)    shift; exec python3 -m unittest discover -s tests -t . -v "$@" ;;
  *) echo "usage: $0 {extract|prepare|vendor|emit|serve|test} [args]" >&2; exit 2 ;;
esac
