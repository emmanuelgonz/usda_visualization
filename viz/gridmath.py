"""Web Mercator tile arithmetic and the canonical CPC grid definition."""

import math

WEB_MERCATOR_HALF_SPAN = 20037508.342789244
TILE_SIZE = 256

# The maximum CPC extent observed across the archive. Masks are written on this
# grid; CPC rasters keep their own per-week extents and are sampled by
# coordinate, so the two never need to nest in pixel space.
CPC_GRID = {
    "width": 508,
    "height": 320,
    "origin_x": -2309800.213402342982590,
    "origin_y": 3185470.286793155129999,
    "pixel_x": 8999.255456289853100,
    "pixel_y": -8995.486488541766448,
    "srs": "EPSG:5070",
}


def tile_bounds(z, x, y):
    """EPSG:3857 (minx, miny, maxx, maxy) of one XYZ tile, y increasing southward."""
    span = 2.0 * WEB_MERCATOR_HALF_SPAN / (2 ** z)
    minx = -WEB_MERCATOR_HALF_SPAN + x * span
    maxy = WEB_MERCATOR_HALF_SPAN - y * span
    return (minx, maxy - span, minx + span, maxy)


def lonlat_to_tile(lon, lat, z):
    """XYZ tile containing a longitude and latitude at one zoom level."""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return (max(0, min(n - 1, x)), max(0, min(n - 1, y)))


def cpc_grid_bounds():
    """EPSG:5070 (minx, miny, maxx, maxy) of the canonical CPC grid."""
    minx = CPC_GRID["origin_x"]
    maxy = CPC_GRID["origin_y"]
    maxx = minx + CPC_GRID["width"] * CPC_GRID["pixel_x"]
    miny = maxy + CPC_GRID["height"] * CPC_GRID["pixel_y"]
    return (minx, miny, maxx, maxy)
