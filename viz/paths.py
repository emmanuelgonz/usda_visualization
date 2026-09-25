"""Every filesystem location the project uses, defined once."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CPC_ARCHIVES = ROOT / "usda_crop_progress_and_condition_gridded_layers"
CDL_ARCHIVES = ROOT / "usda_cropland_data_layer"

DATA = ROOT / "data"
CPC_DATA = DATA / "cpc"
CDL_DATA = DATA / "cdl"
MASK_DATA = DATA / "masks"
CATALOG = DATA / "catalog.json"
EMIT_DATA = DATA / "emit"
EMIT_FOOTPRINTS = EMIT_DATA / "footprints.geojson"
ECO_DATA = DATA / "eco"
ECO_FOOTPRINTS = ECO_DATA / "footprints.geojson"
HLS_DATA = DATA / "hls"
HLS_DB = HLS_DATA / "hls.sqlite"

CACHE = ROOT / "cache"
TILE_CACHE = CACHE / "tiles"

WEB = ROOT / "viz" / "web"
BASINS2 = WEB / "vendor" / "basins2.geojson"
BASINS4 = WEB / "vendor" / "basins4.geojson"
