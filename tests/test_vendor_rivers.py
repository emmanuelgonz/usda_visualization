import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from osgeo import ogr, osr

from viz import vendor_rivers


def line(x0, y0, x1, y1):
    geometry = ogr.Geometry(ogr.wkbLineString)
    geometry.AddPoint(x0, y0)
    geometry.AddPoint(x1, y1)
    return geometry


class RiverFixtures(unittest.TestCase):
    """A HydroRIVERS-like layer and two Natural Earth-like layers, all EPSG:4326.

    The HydroRIVERS layer carries reaches of Strahler order 7 down to 3 near
    lon -100, lat 40 (inside CONUS), a second order-6 reach so that order's
    grouped feature holds two parts, and one order-8 reach near lon -60,
    lat 55 (outside CONUS, both east and north of the box). The two Natural
    Earth layers carry named rivers, an unnamed river, a lake centerline, a
    canal, and one named river (Fraser) north of the CONUS box.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        driver = ogr.GetDriverByName("ESRI Shapefile")

        cls.hydro_ds = driver.CreateDataSource(str(cls.tmp / "hydro.shp"))
        cls.hydro_layer = cls.hydro_ds.CreateLayer("hydro", srs, ogr.wkbLineString)
        cls.hydro_layer.CreateField(ogr.FieldDefn("ORD_STRA", ogr.OFTInteger))
        for order, box in ((7, (-100.123456, 40.654321, -100.0, 40.01)),
                           (6, (-100.02, 40.0, -100.01, 40.01)),
                           (6, (-100.06, 40.0, -100.05, 40.01)),
                           (5, (-100.03, 40.0, -100.02, 40.01)),
                           (4, (-100.04, 40.0, -100.03, 40.01)),
                           (3, (-100.05, 40.0, -100.04, 40.01)),
                           (8, (-60.0, 55.0, -60.01, 55.01))):
            feature = ogr.Feature(cls.hydro_layer.GetLayerDefn())
            feature.SetField("ORD_STRA", order)
            feature.SetGeometry(line(*box))
            cls.hydro_layer.CreateFeature(feature)
        cls.hydro_layer.SyncToDisk()

        cls.ne_a_ds = driver.CreateDataSource(str(cls.tmp / "ne_a.shp"))
        cls.ne_a_layer = cls.ne_a_ds.CreateLayer("ne_a", srs, ogr.wkbLineString)
        cls.ne_a_layer.CreateField(ogr.FieldDefn("name", ogr.OFTString))
        cls.ne_a_layer.CreateField(ogr.FieldDefn("featurecla", ogr.OFTString))
        for name, featurecla, box in (("Missouri", "River", (-100.1, 40.0, -100.0, 40.1)),
                                      ("", "River", (-100.2, 40.0, -100.1, 40.1)),
                                      ("Lake Sakakawea", "Lake Centerline", (-100.3, 40.0, -100.2, 40.1))):
            feature = ogr.Feature(cls.ne_a_layer.GetLayerDefn())
            feature.SetField("name", name)
            feature.SetField("featurecla", featurecla)
            feature.SetGeometry(line(*box))
            cls.ne_a_layer.CreateFeature(feature)
        cls.ne_a_layer.SyncToDisk()

        cls.ne_b_ds = driver.CreateDataSource(str(cls.tmp / "ne_b.shp"))
        cls.ne_b_layer = cls.ne_b_ds.CreateLayer("ne_b", srs, ogr.wkbLineString)
        cls.ne_b_layer.CreateField(ogr.FieldDefn("name", ogr.OFTString))
        cls.ne_b_layer.CreateField(ogr.FieldDefn("featurecla", ogr.OFTString))
        for name, featurecla, box in (("Yellowstone", "River (Intermittent)", (-100.4, 40.0, -100.3, 40.1)),
                                      ("Erie Canal", "Canal", (-100.5, 40.0, -100.4, 40.1)),
                                      ("Fraser", "River", (-121.0, 49.5, -120.9, 49.6))):
            feature = ogr.Feature(cls.ne_b_layer.GetLayerDefn())
            feature.SetField("name", name)
            feature.SetField("featurecla", featurecla)
            feature.SetGeometry(line(*box))
            cls.ne_b_layer.CreateFeature(feature)
        cls.ne_b_layer.SyncToDisk()

    @classmethod
    def tearDownClass(cls):
        cls.hydro_ds = None
        cls.ne_a_ds = None
        cls.ne_b_ds = None
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestBandFeatures(RiverFixtures):
    def test_order_6_and_above_returns_two_features_ord_6_then_7(self):
        features = vendor_rivers.band_features(self.hydro_layer, 6, 99)
        self.assertEqual([f["properties"]["ord"] for f in features], [6, 7])
        for feature in features:
            self.assertEqual(feature["geometry"]["type"], "MultiLineString")
        self.assertEqual(len(features[0]["geometry"]["coordinates"]), 2)   # two order-6 reaches
        self.assertEqual(len(features[1]["geometry"]["coordinates"]), 1)   # one order-7 reach

    def test_orders_4_and_5_returns_two_features_ord_4_then_5(self):
        features = vendor_rivers.band_features(self.hydro_layer, 4, 5)
        self.assertEqual([f["properties"]["ord"] for f in features], [4, 5])
        for feature in features:
            self.assertEqual(feature["geometry"]["type"], "MultiLineString")
            self.assertEqual(len(feature["geometry"]["coordinates"]), 1)

    def test_order_8_outside_conus_never_appears_in_either_band(self):
        for low, high in vendor_rivers.BANDS.values():
            features = vendor_rivers.band_features(self.hydro_layer, low, high)
            self.assertNotIn(8, [f["properties"]["ord"] for f in features])

    def test_coordinates_carry_at_most_4_decimals(self):
        features = vendor_rivers.band_features(self.hydro_layer, 6, 99)
        self.assertTrue(features)
        order_7 = next(f for f in features if f["properties"]["ord"] == 7)
        self.assertIn([-100.1235, 40.6543], order_7["geometry"]["coordinates"][0])
        for feature in features:
            for part in feature["geometry"]["coordinates"]:
                for lon, lat in part:
                    self.assertEqual(round(lon, 4), lon)
                    self.assertEqual(round(lat, 4), lat)


class TestNamedFeatures(RiverFixtures):
    def test_returns_only_named_rivers_in_the_box_in_layer_order(self):
        features = vendor_rivers.named_features([self.ne_a_layer, self.ne_b_layer])
        self.assertEqual([f["properties"]["name"] for f in features], ["Missouri", "Yellowstone"])


class TestMain(RiverFixtures):
    def test_writes_three_files_and_prints_the_summary_line(self):
        out6 = self.tmp / "out6.geojson"
        out4 = self.tmp / "out4.geojson"
        out_named = self.tmp / "out_named.geojson"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = vendor_rivers.main([str(self.tmp / "hydro.shp"), str(self.tmp / "ne_a.shp"),
                                       str(self.tmp / "ne_b.shp"), str(out6), str(out4), str(out_named)])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out6.read_text())["features"]), 2)
        self.assertEqual(len(json.loads(out4.read_text())["features"]), 2)
        self.assertEqual(len(json.loads(out_named.read_text())["features"]), 2)
        self.assertEqual(stdout.getvalue().strip(),
                         f"rivers: 3 reaches of order 6+ -> {out6}; 2 of orders 4-5 -> {out4}; "
                         f"2 named -> {out_named}")
        self.assertFalse(list(self.tmp.glob("*.part")))

    def test_wrong_argument_count_returns_2(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = vendor_rivers.main(["one", "two"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
