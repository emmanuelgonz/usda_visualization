"""Dissolve the USGS 1:250,000 hydrologic units into HUC2 regions and HUC4 subregions.

Run by ./run.sh vendor. The 1994 huc250k shapefile carries every cataloging
unit (HUC8) with its REG and SUB codes but no region or subregion names;
those come from USGS's huc_name.txt, the text of Water-Supply Paper 2294.
Each level is written as GeoJSON in WGS84 with huc, name, and a label point
inside the largest polygon, simplified for drawing at map scale.
"""

import json
import os
import re
import sys

from osgeo import ogr, osr

ogr.UseExceptions()
osr.UseExceptions()

PRECISION = 4                       # decimal degrees, about 11 m
SIMPLIFY = {2: 0.01, 4: 0.004}      # degrees; about 1 km for regions, 400 m for subregions
_REGION_RE = re.compile(r"^Region (\d{2})\s+(.+?)\s*--", re.M)
_SUBREGION_RE = re.compile(r"^[ \t]*Su(?:b)?region[ \t]+(\d{4})[ \t]*--[ \t]*(.*)$", re.M)   # "Suregion" is in the file
# A name ends at a colon or at a sentence period; "St." and the like are not sentence ends.
_NAME_END_RE = re.compile(r":|(?<!\bSt)(?<!\bMt)(?<!\bFt)\.(?=\s|$)")


def _subregion_name(first, rest):
    """The name from an entry's first line, joined with the next when the name wraps.

    Names end at a colon; a few end at a period instead ("Yakima. The Yakima
    River Basin."), and one wraps onto the next line before its colon.
    """
    line = first.strip()
    if not _NAME_END_RE.search(line) and rest:
        line = line + " " + rest.strip()
    match = _NAME_END_RE.search(line)
    name = line[:match.start()] if match else line
    return " ".join(name.split())


def parse_names(text):
    """({'10': 'Missouri Region', ...}, {'1027': 'Kansas', ...}) from huc_name.txt."""
    regions = {code: " ".join(name.split()) for code, name in _REGION_RE.findall(text)}
    lines = text.splitlines()
    subregions = {}
    for number, line in enumerate(lines):
        match = _SUBREGION_RE.match(line)
        if match:
            rest = lines[number + 1] if number + 1 < len(lines) else ""
            subregions[match.group(1)] = _subregion_name(match.group(2), rest)
    return regions, subregions


def label_point(geometry):
    """(lon, lat) inside the largest polygon of a (multi)polygon."""
    largest = geometry
    if geometry.GetGeometryType() in (ogr.wkbMultiPolygon, ogr.wkbMultiPolygon25D):
        largest = max((geometry.GetGeometryRef(i) for i in range(geometry.GetGeometryCount())),
                      key=lambda g: g.GetArea())
    point = largest.PointOnSurface()
    return (point.GetX(), point.GetY())


def dissolve(layer, level, names):
    """GeoJSON features for one level: unions of the HUC8 polygons sharing a code prefix.

    level is 2 or 4. Polygons are repaired before the union, unioned in the
    layer's own projection, then transformed to WGS84 and simplified.
    """
    field = {2: "REG", 4: "SUB"}[level]
    groups = {}
    layer.ResetReading()
    for feature in layer:
        code = str(feature.GetField(field))
        geometry = feature.GetGeometryRef().Clone()
        if not geometry.IsValid():
            geometry = geometry.MakeValid()
        groups.setdefault(code, []).append(geometry)

    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    source = layer.GetSpatialRef()
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)

    features = []
    for code in sorted(groups):
        collection = ogr.Geometry(ogr.wkbMultiPolygon)
        for geometry in groups[code]:
            if geometry.GetGeometryType() in (ogr.wkbMultiPolygon, ogr.wkbMultiPolygon25D):
                for i in range(geometry.GetGeometryCount()):
                    collection.AddGeometry(geometry.GetGeometryRef(i))
            else:
                collection.AddGeometry(geometry)
        union = collection.UnionCascaded()
        union.Transform(transform)
        union = union.SimplifyPreserveTopology(SIMPLIFY[level])
        lon, lat = label_point(union)
        features.append({
            "type": "Feature",
            "geometry": json.loads(union.ExportToJson([f"COORDINATE_PRECISION={PRECISION}"])),
            "properties": {"huc": code, "name": names.get(code, f"HUC {code}"),
                           "label_lon": round(lon, PRECISION), "label_lat": round(lat, PRECISION)},
        })
    return features


def write(features, path):
    tmp = path + ".part"
    with open(tmp, "w") as handle:
        json.dump({"type": "FeatureCollection", "features": features}, handle)
    os.replace(tmp, path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4:
        print("usage: vendor_basins SHAPEFILE NAMES_TXT OUT_HUC2 OUT_HUC4", file=sys.stderr)
        return 2
    shapefile, names_path, out2, out4 = argv
    with open(names_path, encoding="latin-1") as handle:
        regions, subregions = parse_names(handle.read())
    source = ogr.Open(shapefile)
    layer = source.GetLayer(0)
    huc2 = dissolve(layer, 2, regions)
    huc4 = dissolve(layer, 4, subregions)
    write(huc2, out2)
    write(huc4, out4)
    unnamed = [f["properties"]["huc"] for f in huc4 if f["properties"]["name"].startswith("HUC ")]
    print(f"basins: {len(huc2)} regions -> {out2}; {len(huc4)} subregions -> {out4}"
          + (f"; unnamed subregions: {', '.join(unnamed)}" if unnamed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
