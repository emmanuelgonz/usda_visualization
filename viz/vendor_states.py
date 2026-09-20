"""Convert the Census cartographic state boundaries to CONUS-only GeoJSON.

Run once by ``./run.sh vendor``. The cartographic boundary product carries no
interior-point fields (those live in the full TIGER/Line files), so a label
point is computed here: the point-on-surface of the LARGEST polygon in each
state, which keeps Michigan's label on the Lower Peninsula rather than the UP.
"""

import os
import sys

from osgeo import ogr, osr

ogr.UseExceptions()
osr.UseExceptions()

# Alaska, Hawaii, American Samoa, Guam, Northern Mariana Islands, Puerto Rico,
# US Virgin Islands. Everything else in the file is CONUS plus DC.
EXCLUDED_STATEFP = frozenset({"02", "15", "60", "66", "69", "72", "78"})

FIELDS = ("NAME", "STUSPS")
PRECISION = 4  # decimal degrees, about 11 m, inside the 1:5M file's own precision


def label_point(geometry):
    """(lon, lat) guaranteed inside the largest polygon of a (multi)polygon."""
    largest = geometry
    if geometry.GetGeometryType() in (ogr.wkbMultiPolygon, ogr.wkbMultiPolygon25D):
        largest = max((geometry.GetGeometryRef(i) for i in range(geometry.GetGeometryCount())),
                      key=lambda g: g.GetArea())
    point = largest.PointOnSurface()
    return (point.GetX(), point.GetY())


def convert(src_path, out_path):
    """Write CONUS states with NAME, STUSPS, INTPTLAT, INTPTLON. Returns the count."""
    src = ogr.Open(src_path)
    layer = src.GetLayer(0)

    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    source_srs = layer.GetSpatialRef()
    source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source_srs, target)

    driver = ogr.GetDriverByName("GeoJSON")
    if os.path.exists(out_path):
        os.remove(out_path)
    out = driver.CreateDataSource(out_path)
    out_layer = out.CreateLayer("states", target, ogr.wkbMultiPolygon,
                                options=[f"COORDINATE_PRECISION={PRECISION}"])
    for name in FIELDS:
        out_layer.CreateField(ogr.FieldDefn(name, ogr.OFTString))
    for name in ("INTPTLAT", "INTPTLON"):
        out_layer.CreateField(ogr.FieldDefn(name, ogr.OFTReal))

    count = 0
    for feature in layer:
        if feature.GetField("STATEFP") in EXCLUDED_STATEFP:
            continue
        geometry = feature.GetGeometryRef().Clone()
        geometry.Transform(transform)
        lon, lat = label_point(geometry)

        new = ogr.Feature(out_layer.GetLayerDefn())
        for name in FIELDS:
            new.SetField(name, feature.GetField(name))
        new.SetField("INTPTLAT", round(lat, PRECISION))
        new.SetField("INTPTLON", round(lon, PRECISION))
        new.SetGeometry(geometry)
        out_layer.CreateFeature(new)
        count += 1

    out = None
    return count


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python3 -m viz.vendor_states <source.shp|/vsizip/...> <out.geojson>", file=sys.stderr)
        return 2
    count = convert(argv[0], argv[1])
    print(f"states: {count} CONUS features written to {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
