"""Vendor river lines for the map: HydroRIVERS reaches and Natural Earth named rivers.

Run by ./run.sh vendor. HydroRIVERS v1.0 North America carries every mapped
reach with a Strahler stream order (ORD_STRA); it is split into two bands so
the browser can draw order 6 and above always and orders 4-5 only when zoomed
in, keeping the always-on file small. Reaches of the same order are merged
into a single MultiLineString feature per order, so Leaflet draws one path
per order instead of one per reach. The Natural Earth 10 m rivers and lake
centerlines, together with its North America supplement, carry the handful of
reaches with common names (the Missouri, the Yellowstone, and the like); only
the named, river-classed features are kept, one per reach. All three outputs
are filtered to CONUS and written as GeoJSON in WGS84 with viz.vendor_basins.write.
"""

import json
import sys

from osgeo import ogr, osr

from viz import vendor_basins

ogr.UseExceptions()
osr.UseExceptions()

CONUS = (-125.0, 24.4, -66.9, 49.4)   # lon_min, lat_min, lon_max, lat_max
PRECISION = 4                          # decimal degrees
BANDS = {"6": (6, 99), "4": (4, 5)}    # file key -> inclusive Strahler order range
NAMED_CLASSES = ("River", "River (Intermittent)")


def to_wgs84(layer):
    """A CoordinateTransformation to EPSG:4326, or None when already geographic WGS84."""
    srs = layer.GetSpatialRef()
    if srs is not None and srs.IsGeographic():
        if srs.GetAuthorityCode(None) == "4326" or srs.GetAttrValue("DATUM") in (
                "WGS_1984", "World Geodetic System 1984"):
            return None
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(srs, target)


def _exported(geometry, transform):
    if transform is not None:
        geometry.Transform(transform)
    return json.loads(geometry.ExportToJson([f"COORDINATE_PRECISION={PRECISION}"]))


def reaches(layer, low, high):
    """(geometry_json_dict, order) for CONUS reaches with ORD_STRA in [low, high]."""
    layer.SetSpatialFilterRect(*CONUS)
    layer.ResetReading()
    transform = to_wgs84(layer)
    for feature in layer:
        order = feature.GetField("ORD_STRA")
        if order is None or not (low <= order <= high):
            continue
        yield _exported(feature.GetGeometryRef().Clone(), transform), order


def named_rivers(layer):
    """(geometry_json_dict, name) for CONUS features that are named rivers."""
    layer.SetSpatialFilterRect(*CONUS)
    layer.ResetReading()
    transform = to_wgs84(layer)
    for feature in layer:
        if feature.GetField("featurecla") not in NAMED_CLASSES:
            continue
        name = (feature.GetField("name") or "").strip()
        if not name:
            continue
        yield _exported(feature.GetGeometryRef().Clone(), transform), name


def _grouped_by_order(layer, low, high):
    """(features, reach_count): one MultiLineString Feature per distinct order, ascending.

    A reach that is itself a MultiLineString contributes each of its parts to
    its order's group, rather than nesting a MultiLineString inside another.
    reach_count is the number of reaches folded in, independent of how many
    parts they contributed.
    """
    groups = {}
    count = 0
    for geometry, order in reaches(layer, low, high):
        count += 1
        parts = groups.setdefault(order, [])
        if geometry["type"] == "MultiLineString":
            parts.extend(geometry["coordinates"])
        else:
            parts.append(geometry["coordinates"])
    features = [{"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": groups[order]},
                 "properties": {"ord": order}} for order in sorted(groups)]
    return features, count


def band_features(layer, low, high):
    """One MultiLineString Feature per distinct Strahler order present, ascending."""
    features, _ = _grouped_by_order(layer, low, high)
    return features


def named_features(layers):
    features = []
    for layer in layers:
        for geometry, name in named_rivers(layer):
            features.append({"type": "Feature", "geometry": geometry, "properties": {"name": name}})
    return features


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6:
        print("usage: vendor_rivers HYDRORIVERS_SHP NE_RIVERS_SHP NE_RIVERS_NA_SHP OUT6 OUT4 OUT_NAMED",
              file=sys.stderr)
        return 2
    hydro_path, ne_path, ne_na_path, out6, out4, out_named = argv

    hydro_source = ogr.Open(hydro_path)
    hydro_layer = hydro_source.GetLayer(0)
    band6, n6 = _grouped_by_order(hydro_layer, *BANDS["6"])
    band4, n4 = _grouped_by_order(hydro_layer, *BANDS["4"])

    ne_source = ogr.Open(ne_path)
    ne_layer = ne_source.GetLayer(0)
    ne_na_source = ogr.Open(ne_na_path)
    ne_na_layer = ne_na_source.GetLayer(0)
    named = named_features([ne_layer, ne_na_layer])

    vendor_basins.write(band6, out6)
    vendor_basins.write(band4, out4)
    vendor_basins.write(named, out_named)

    print(f"rivers: {n6} reaches of order 6+ -> {out6}; {n4} of orders 4-5 -> {out4}; "
          f"{len(named)} named -> {out_named}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
