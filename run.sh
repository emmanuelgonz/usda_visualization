#!/usr/bin/env bash
# Every entry point runs with PYTHONNOUSERSITE=1 so the system GDAL bindings
# see NumPy 1.21.5 rather than the NumPy 2.x in the user site directory.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONNOUSERSITE=1

case "${1:-serve}" in
  extract) shift; exec python3 -m viz.extract "$@" ;;
  prepare) shift; exec python3 -m viz.prepare "$@" ;;
  serve)   shift; exec python3 -m viz.tileserver "$@" ;;
  # -t . keeps the repo root as the top-level import dir so `from viz import ...`
  # and `from tests import fixtures` both resolve.
  test)    shift; exec python3 -m unittest discover -s tests -t . -v "$@" ;;
  *) echo "usage: $0 {extract|prepare|serve|test} [args]" >&2; exit 2 ;;
esac
