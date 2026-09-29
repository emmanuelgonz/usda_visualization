"""Vendor river lines for the map: HydroRIVERS reaches carrying Natural Earth names.

Run by ./run.sh vendor. HydroRIVERS v1.0 North America carries every mapped
reach with a Strahler stream order (ORD_STRA), a downstream link (NEXT_DOWN),
mean discharge (DIS_AV_CMS), and upstream area (UPLAND_SKM). Reaches of order 4
and above are kept and split into two bands so the browser can draw order 6
and above always and orders 4-5 only when zoomed in, keeping the always-on
file small. Each band file holds one MultiLineString feature per Strahler
order, so Leaflet draws one path per order, with per-part arrays "dis"
(discharge, m3/s, one decimal), "up" (upstream area, km2, integer), and "name"
(a Natural Earth river name or null) in the same sequence as the parts.

HydroRIVERS carries no names. The Natural Earth 10 m rivers and lake
centerlines, together with its North America supplement, supply named
reference lines (River, River (Intermittent), Lake Centerline), and
viz.river_names joins them onto reaches: a reach hugging one reference line
takes its name, and the name then propagates along the flow network. Both
outputs are filtered to CONUS and written as GeoJSON in WGS84 with
viz.vendor_basins.write.
"""

import json
import sys

from osgeo import ogr, osr

from viz import river_names, vendor_basins

ogr.UseExceptions()
osr.UseExceptions()

CONUS = (-125.0, 24.4, -66.9, 49.4)   # lon_min, lat_min, lon_max, lat_max
PRECISION = 4                          # decimal degrees
BANDS = {"6": (6, 99), "4": (4, 5)}    # file key -> inclusive Strahler order range
REFERENCE_CLASSES = ("River", "River (Intermittent)", "Lake Centerline")
MIN_ORDER = 4                          # lowest Strahler order kept


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


def load_reaches(layer):
    """{HYRIV_ID: reach} for CONUS reaches with ORD_STRA >= MIN_ORDER (see viz.river_names)."""
    layer.SetSpatialFilterRect(*CONUS)
    layer.ResetReading()
    transform = to_wgs84(layer)
    reaches = {}
    for feature in layer:
        order = feature.GetField("ORD_STRA")
        if order is None or order < MIN_ORDER:
            continue
        geometry = _exported(feature.GetGeometryRef().Clone(), transform)
        parts = geometry["coordinates"] if geometry["type"] == "MultiLineString" else [geometry["coordinates"]]
        reaches[feature.GetField("HYRIV_ID")] = {
            "next": feature.GetField("NEXT_DOWN") or None,
            "ord": order,
            "dis": feature.GetField("DIS_AV_CMS"),
            "up": feature.GetField("UPLAND_SKM"),
            "parts": parts,
        }
    return reaches


def reference_lines(layers):
    """[(name, line)] for named river-classed Natural Earth features in CONUS, one per part."""
    lines = []
    for layer in layers:
        layer.SetSpatialFilterRect(*CONUS)
        layer.ResetReading()
        transform = to_wgs84(layer)
        for feature in layer:
            if feature.GetField("featurecla") not in REFERENCE_CLASSES:
                continue
            name = (feature.GetField("name") or "").strip()
            if not name:
                continue
            geometry = _exported(feature.GetGeometryRef().Clone(), transform)
            parts = geometry["coordinates"] if geometry["type"] == "MultiLineString" else [geometry["coordinates"]]
            lines.extend((name, part) for part in parts)
    return lines


def band_features(reaches, names, low, high):
    """One MultiLineString Feature per distinct order in [low, high], ascending, with per-part arrays."""
    groups = {}
    for reach_id in sorted(reaches):
        reach = reaches[reach_id]
        if not low <= reach["ord"] <= high:
            continue
        group = groups.setdefault(reach["ord"], {"parts": [], "dis": [], "up": [], "name": []})
        for part in reach["parts"]:
            group["parts"].append(part)
            group["dis"].append(round(reach["dis"], 1))
            group["up"].append(int(round(reach["up"])))
            group["name"].append(names.get(reach_id))
    return [{"type": "Feature",
             "geometry": {"type": "MultiLineString", "coordinates": groups[order]["parts"]},
             "properties": {"ord": order, "dis": groups[order]["dis"], "up": groups[order]["up"],
                            "name": groups[order]["name"]}}
            for order in sorted(groups)]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 5:
        print("usage: vendor_rivers HYDRORIVERS_SHP NE_RIVERS_SHP NE_RIVERS_NA_SHP OUT6 OUT4", file=sys.stderr)
        return 2
    hydro_path, ne_path, ne_na_path, out6, out4 = argv

    hydro_source = ogr.Open(hydro_path)
    ne_source = ogr.Open(ne_path)
    ne_na_source = ogr.Open(ne_na_path)
    reaches = load_reaches(hydro_source.GetLayer(0))
    references = reference_lines([ne_source.GetLayer(0), ne_na_source.GetLayer(0)])
    names = river_names.assign_names(reaches, references)

    band6 = band_features(reaches, names, *BANDS["6"])
    band4 = band_features(reaches, names, *BANDS["4"])
    vendor_basins.write(band6, out6)
    vendor_basins.write(band4, out4)

    n6 = sum(1 for r in reaches.values() if BANDS["6"][0] <= r["ord"] <= BANDS["6"][1])
    n4 = sum(1 for r in reaches.values() if BANDS["4"][0] <= r["ord"] <= BANDS["4"][1])
    print(f"rivers: {n6} reaches of order 6+ -> {out6}; {n4} of orders 4-5 -> {out4}; "
          f"{len(names)} reaches named from {len(set(names.values()))} Natural Earth rivers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
