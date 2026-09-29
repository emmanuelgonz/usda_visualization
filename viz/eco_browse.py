"""Browse images for ECOSTRESS swaths, found through the tiled sibling collection.

The swath collection (ECO_L2_LSTE) has no browse images. The tiled collection
(ECO_L2T_LSTE) has one granule per MGRS tile per scene, each with public browse
JPEGs, so a point inside a swath resolves to the tile granule that covers it.
"""

import re
import urllib.parse
from collections import OrderedDict

from viz import cmr

TILED_SHORT_NAME = "ECO_L2T_LSTE"
TILED_VERSION = "002"
BROWSE_KINDS = {"lst": "_LST.jpeg", "qc": "_QC.jpeg", "cloud": "_cloud.jpeg"}
CACHE_CAP = 256

SWATH_RE = re.compile(r"^ECOv002_L2_LSTE_(\d+)_(\d+)_\d{8}T\d{6}_\d+_\d+$")

_cache = OrderedDict()
_MISS = object()


def parse_swath_id(swath_id):
    """(orbit, scene) strings from a swath granule id, or ValueError."""
    match = SWATH_RE.match(swath_id)
    if not match:
        raise ValueError("not an ECOSTRESS swath id (ECOv002_L2_LSTE_orbit_scene_...)")
    return match.group(1), match.group(2)


def query_url(orbit, scene, lon, lat):
    """CMR search for the tiled granules of one scene that contain a point."""
    params = [
        ("short_name", TILED_SHORT_NAME),
        ("version", TILED_VERSION),
        ("page_size", 4),
        ("readable_granule_name[]", f"ECOv002_L2T_LSTE_{orbit}_{scene}_*"),
        ("options[readable_granule_name][pattern]", "true"),
        ("point", f"{lon:.6f},{lat:.6f}"),
    ]
    return cmr.CMR_URL + "?" + urllib.parse.urlencode(params)


def browse_links(entry):
    """{"lst", "qc", "cloud"} browse hrefs from an entry; s3:// links are ignored."""
    found = {}
    for item in entry.get("links", []):
        href = item.get("href", "")
        if not href.startswith("http") or "browse" not in item.get("rel", ""):
            continue
        for kind, suffix in BROWSE_KINDS.items():
            if href.endswith(suffix) and kind not in found:
                found[kind] = href
    return found


def tile_from_id(tiled_id):
    """MGRS tile of a tiled granule id, such as 15TUF."""
    return tiled_id.split("_")[5]


def clear_cache():
    _cache.clear()


def lookup(swath_id, lon, lat, fetch=None):
    """The tiled granule with browse links at a point for a swath, or None.

    Raises ValueError for a malformed swath id. CMR failures propagate.
    fetch defaults to cmr.fetch_page, resolved at call time.
    """
    orbit, scene = parse_swath_id(swath_id)
    key = (swath_id, round(lon, 3), round(lat, 3))
    hit = _cache.get(key, _MISS)
    if hit is not _MISS:
        return hit
    fetch = fetch or cmr.fetch_page
    result = None
    for entry in fetch(query_url(orbit, scene, lon, lat)):
        links = browse_links(entry)
        if "lst" not in links:
            continue
        tiled_id = entry.get("producer_granule_id") or entry["title"]
        result = {"id": tiled_id, "tile": tile_from_id(tiled_id), "start": entry["time_start"],
                  "daynight": entry.get("day_night_flag"), "browse": links}
        break
    _cache[key] = result
    while len(_cache) > CACHE_CAP:
        _cache.popitem(last=False)
    return result
