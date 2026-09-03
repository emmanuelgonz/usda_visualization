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

CACHE = ROOT / "cache"
TILE_CACHE = CACHE / "tiles"

WEB = ROOT / "viz" / "web"
