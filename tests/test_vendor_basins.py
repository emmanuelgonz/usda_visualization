import json
import shutil
import tempfile
import unittest
from pathlib import Path

from osgeo import ogr, osr

from viz import vendor_basins

NAMES = """
Region 03  South Atlantic-Gulf Region--The drainage that ultimately
           discharges into the Atlantic.

  Subregion  0301 -- Chowan-Roanoke: The coastal drainage and
                     the Roanoke River Basin.

Region 10  Missouri Region -- The drainage within the United states of:
           (a) the Missouri River Basin.

  Subregion  1027 -- Kansas: The Kansas River Basin. Kansas.
                       Area =    60000 sq.mi.

  Subregion  0415 -- Northeastern Lake Ontario-Lake
                     Ontario-St. Lawrence: The drainage into Lake Ontario.

  Subregion  0404 -- Southwestern Lake Michigan. The drainage into Lake
                     Michigan from the St. Joseph River Basin boundary.

  Subregion  0405 -- Southeastern Lake Michigan: The drainage into Lake
                     Michigan from and including the St. Joseph River.

   Suregion  0705 -- Chippewa: The Chippewa River Basin. Michigan,
                     Wisconsin.

  Subregion  1703 -- Yakima. The Yakima River Basin. Washington.

  Subregion  0199 -- St. Marys. The St. Marys River Basin. Georgia.
"""


class TestParseNames(unittest.TestCase):
    def test_regions_with_and_without_spaces_around_the_dashes(self):
        regions, _ = vendor_basins.parse_names(NAMES)
        self.assertEqual(regions, {"03": "South Atlantic-Gulf Region", "10": "Missouri Region"})

    def test_subregion_names_stop_at_the_colon_and_join_wrapped_lines(self):
        _, subregions = vendor_basins.parse_names(NAMES)
        self.assertEqual(subregions, {"0301": "Chowan-Roanoke", "1027": "Kansas",
                                      "0415": "Northeastern Lake Ontario-Lake Ontario-St. Lawrence",
                                      "0404": "Southwestern Lake Michigan",      # period instead of a colon
                                      "0405": "Southeastern Lake Michigan",      # must not be swallowed by 0404
                                      "0705": "Chippewa",                        # the file's "Suregion" typo
                                      "1703": "Yakima",
                                      "0199": "St. Marys"})                     # "St." is not a sentence end


def square(x0, y0, x1, y1):
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)):
        ring.AddPoint(x, y)
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)
    return polygon


class TestDissolve(unittest.TestCase):
    """Three HUC8 squares in an Albers projection: two in subregion 1001, one in 1002, all in region 10."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        srs = osr.SpatialReference()
        srs.ImportFromProj4("+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +datum=NAD27 +units=m")
        driver = ogr.GetDriverByName("ESRI Shapefile")
        self.ds = driver.CreateDataSource(str(self.tmp / "huc.shp"))
        self.layer = self.ds.CreateLayer("huc", srs, ogr.wkbPolygon)
        for name in ("HUC_CODE", "REG", "SUB"):
            self.layer.CreateField(ogr.FieldDefn(name, ogr.OFTString))
        # Squares are 100 km wide, side by side, around 40 N 100 W (x about 340 km, y about 1.9e6 m).
        for code, box in (("10010001", (300000, 1900000, 400000, 2000000)),
                          ("10010002", (400000, 1900000, 500000, 2000000)),
                          ("10020001", (300000, 2000000, 400000, 2100000))):
            feature = ogr.Feature(self.layer.GetLayerDefn())
            feature.SetField("HUC_CODE", code)
            feature.SetField("REG", code[:2])
            feature.SetField("SUB", code[:4])
            feature.SetGeometry(square(*box))
            self.layer.CreateFeature(feature)
        self.layer.SyncToDisk()

    def test_huc4_unions_the_squares_of_each_subregion(self):
        features = vendor_basins.dissolve(self.layer, 4, {"1001": "Upper Test", "1002": "Lower Test"})
        self.assertEqual([f["properties"]["huc"] for f in features], ["1001", "1002"])
        self.assertEqual([f["properties"]["name"] for f in features], ["Upper Test", "Lower Test"])
        upper = features[0]["geometry"]
        self.assertEqual(upper["type"], "Polygon")                  # two adjacent squares become one ring
        lons = [p[0] for p in upper["coordinates"][0]]
        lats = [p[1] for p in upper["coordinates"][0]]
        self.assertLess(max(lons) - min(lons), 3.0)                 # about 200 km wide in degrees
        self.assertGreater(max(lons) - min(lons), 2.0)
        self.assertLess(max(lats) - min(lats), 1.2)

    def test_huc2_unions_everything_and_labels_inside(self):
        features = vendor_basins.dissolve(self.layer, 2, {"10": "Missouri Region"})
        self.assertEqual(len(features), 1)
        props = features[0]["properties"]
        self.assertEqual((props["huc"], props["name"]), ("10", "Missouri Region"))
        geometry = ogr.CreateGeometryFromJson(json.dumps(features[0]["geometry"]))
        point = ogr.Geometry(ogr.wkbPoint)
        point.AddPoint(props["label_lon"], props["label_lat"])
        self.assertTrue(geometry.Contains(point))

    def test_unnamed_code_gets_a_placeholder_name(self):
        features = vendor_basins.dissolve(self.layer, 4, {})
        self.assertEqual(features[0]["properties"]["name"], "HUC 1001")

    def test_main_writes_both_files(self):
        import contextlib
        import io
        names = self.tmp / "names.txt"
        names.write_text(NAMES)
        out2, out4 = self.tmp / "b2.geojson", self.tmp / "b4.geojson"
        self.ds = None                                           # flush the shapefile
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(vendor_basins.main([str(self.tmp / "huc.shp"), str(names), str(out2), str(out4)]), 0)
        self.assertEqual(len(json.loads(out2.read_text())["features"]), 1)
        self.assertEqual(len(json.loads(out4.read_text())["features"]), 2)
        self.assertFalse(list(self.tmp.glob("*.part")))


if __name__ == "__main__":
    unittest.main()
