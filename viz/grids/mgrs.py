"""The MGRS / Sentinel-2 / HLS tile grid: a tile's outline from its id.

A granule's CMR polygon is its data footprint, which at a swath edge is a
clipped piece of the tile, so outlines are computed from the id instead.
Northern hemisphere only, which covers every tile over CONUS.
"""

import re

# MGRS tile ID: T, UTM zone, latitude band, 100 km column letter, 100 km row letter.
_TILE_ID_RE = re.compile(r"^T(\d{2})([C-HJ-NP-X])([A-HJ-NP-Z])([A-HJ-NP-V])$")
_BANDS = "CDEFGHJKLMNPQRSTUVWX"                   # 8-degree bands from 80 S
_COLUMN_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"
TILE_SIDE_M = 109800.0                             # Sentinel-2 / HLS tile side
_transforms = {}


def _transforms_for(zone):
    """(UTM -> WGS84, WGS84 -> UTM) for a northern-hemisphere zone, built once."""
    if zone not in _transforms:
        from osgeo import osr
        osr.UseExceptions()
        utm = osr.SpatialReference()
        utm.ImportFromEPSG(32600 + zone)
        wgs = osr.SpatialReference()
        wgs.ImportFromEPSG(4326)
        for srs in (utm, wgs):
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        _transforms[zone] = (osr.CoordinateTransformation(utm, wgs), osr.CoordinateTransformation(wgs, utm))
    return _transforms[zone]


def ring(tile):
    """The closed [lon, lat] outline of an MGRS/HLS tile computed from its ID, or None if malformed.

    A granule's CMR polygon is its data footprint, which at a swath edge is a
    clipped piece of the tile, so the outline is computed instead: the 100 km
    square's easting comes from the column letter, its northing from the row
    letter (offset by five rows in even zones) resolved to the latitude band,
    and the 109.8 km Sentinel-2 tile hangs from the square's north-west corner.
    Northern hemisphere only, which covers every HLS tile over CONUS.
    """
    match = _TILE_ID_RE.match(tile or "")
    if not match:
        return None
    zone = int(match.group(1))
    band, column, row = match.group(2), match.group(3), match.group(4)
    if not (1 <= zone <= 60) or band < "N":
        return None
    columns = _COLUMN_SETS[(zone - 1) % 3]
    if column not in columns:
        return None
    to_wgs, to_utm = _transforms_for(zone)
    easting = (columns.index(column) + 1) * 100000.0
    row_index = (_ROW_LETTERS.index(row) - (0 if zone % 2 else 5)) % 20
    northing = row_index * 100000.0
    band_min_lat = -80 + 8 * _BANDS.index(band)
    band_min_northing = to_utm.TransformPoint(-183.0 + 6 * zone, float(band_min_lat))[1]
    while northing < band_min_northing - 100000.0:   # a square may start just south of its band
        northing += 2000000.0
    band_max_northing = to_utm.TransformPoint(-183.0 + 6 * zone, float(band_min_lat + 8))[1]
    if northing > band_max_northing:                  # row letter inconsistent with the band
        return None
    top = northing + 100000.0
    corners = [(easting, top - TILE_SIDE_M), (easting + TILE_SIDE_M, top - TILE_SIDE_M),
               (easting + TILE_SIDE_M, top), (easting, top)]
    ring = [[round(x, 6), round(y, 6)] for x, y, _ in (to_wgs.TransformPoint(e, n) for e, n in corners)]
    return ring + [ring[0]]
