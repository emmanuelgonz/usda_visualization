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
    ;;
  serve)
    shift
    if [ ! -f viz/web/vendor/leaflet/leaflet.js ]; then
      echo "Leaflet is not vendored; run ./run.sh vendor first" >&2
      exit 1
    fi
    exec python3 -m viz.tileserver "$@"
    ;;
  # -t . keeps the repo root as the top-level import dir so `from viz import ...`
  # and `from tests import fixtures` both resolve.
  test)    shift; exec python3 -m unittest discover -s tests -t . -v "$@" ;;
  *) echo "usage: $0 {extract|prepare|vendor|serve|test} [args]" >&2; exit 2 ;;
esac
