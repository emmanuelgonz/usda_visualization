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
    The Missouri reference line lies on the first order-6 reach (HYRIV_ID 11), so
    the join names it.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        driver = ogr.GetDriverByName("ESRI Shapefile")

        cls.hydro_ds = driver.CreateDataSource(str(cls.tmp / "hydro.shp"))
        cls.hydro_layer = cls.hydro_ds.CreateLayer("hydro", srs, ogr.wkbLineString)
        for field, kind in (("HYRIV_ID", ogr.OFTInteger), ("NEXT_DOWN", ogr.OFTInteger),
                            ("ORD_STRA", ogr.OFTInteger), ("DIS_AV_CMS", ogr.OFTReal),
                            ("UPLAND_SKM", ogr.OFTReal)):
            cls.hydro_layer.CreateField(ogr.FieldDefn(field, kind))
        # id, next, order, discharge, upstream area, box
        for reach_id, next_id, order, dis, up, box in (
                (10, 0, 7, 1234.56, 98765.4, (-100.123456, 40.654321, -100.0, 40.01)),
                (11, 10, 6, 250.04, 4321.6, (-100.02, 40.0, -100.01, 40.01)),
                (12, 10, 6, 120.0, 2000.0, (-100.06, 40.0, -100.05, 40.01)),
                (13, 11, 5, 40.0, 900.0, (-100.03, 40.0, -100.02, 40.01)),
                (14, 13, 4, 12.0, 300.0, (-100.04, 40.0, -100.03, 40.01)),
                (15, 14, 3, 3.0, 50.0, (-100.05, 40.0, -100.04, 40.01)),
                (16, 0, 8, 9000.0, 900000.0, (-60.0, 55.0, -60.01, 55.01))):
            feature = ogr.Feature(cls.hydro_layer.GetLayerDefn())
            for field, value in (("HYRIV_ID", reach_id), ("NEXT_DOWN", next_id), ("ORD_STRA", order),
                                 ("DIS_AV_CMS", dis), ("UPLAND_SKM", up)):
                feature.SetField(field, value)
            feature.SetGeometry(line(*box))
            cls.hydro_layer.CreateFeature(feature)
        cls.hydro_layer.SyncToDisk()

        cls.ne_a_ds = driver.CreateDataSource(str(cls.tmp / "ne_a.shp"))
        cls.ne_a_layer = cls.ne_a_ds.CreateLayer("ne_a", srs, ogr.wkbLineString)
        cls.ne_a_layer.CreateField(ogr.FieldDefn("name", ogr.OFTString))
        cls.ne_a_layer.CreateField(ogr.FieldDefn("featurecla", ogr.OFTString))
        for name, featurecla, box in (("Missouri", "River", (-100.02, 40.0, -100.01, 40.01)),
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


class TestLoadReaches(RiverFixtures):
    def test_skips_orders_below_4_and_the_out_of_box_reach(self):
        reaches = vendor_rivers.load_reaches(self.hydro_layer)
        self.assertEqual(sorted(reaches), [10, 11, 12, 13, 14])

    def test_fills_the_five_fields(self):
        reaches = vendor_rivers.load_reaches(self.hydro_layer)
        self.assertEqual(reaches[11]["next"], 10)
        self.assertEqual(reaches[11]["ord"], 6)
        self.assertEqual(reaches[11]["dis"], 250.04)
        self.assertEqual(reaches[11]["up"], 4321.6)
        self.assertEqual(reaches[11]["parts"], [[[-100.02, 40.0], [-100.01, 40.01]]])
        self.assertIsNone(reaches[10]["next"])


class TestSwappedSources(unittest.TestCase):
    """Clear failures for a source without a spatial reference or with null geometries."""

    def memory_layer(self, srs, fields):
        dataset = ogr.GetDriverByName("Memory").CreateDataSource("swapped")
        layer = dataset.CreateLayer("swapped", srs, ogr.wkbLineString)
        for field, kind in fields:
            layer.CreateField(ogr.FieldDefn(field, kind))
        return dataset, layer

    def add(self, layer, values, box):
        feature = ogr.Feature(layer.GetLayerDefn())
        for field, value in values.items():
            feature.SetField(field, value)
        if box is not None:
            feature.SetGeometry(line(*box))
        layer.CreateFeature(feature)

    def test_to_wgs84_names_the_layer_without_a_spatial_reference(self):
        dataset, layer = self.memory_layer(None, [("ORD_STRA", ogr.OFTInteger)])
        with self.assertRaisesRegex(ValueError, "swapped has no spatial reference"):
            vendor_rivers.to_wgs84(layer)

    def test_load_reaches_skips_a_null_geometry(self):
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        fields = [("HYRIV_ID", ogr.OFTInteger), ("NEXT_DOWN", ogr.OFTInteger), ("ORD_STRA", ogr.OFTInteger),
                  ("DIS_AV_CMS", ogr.OFTReal), ("UPLAND_SKM", ogr.OFTReal)]
        dataset, layer = self.memory_layer(srs, fields)
        base = {"NEXT_DOWN": 0, "DIS_AV_CMS": 1.0, "UPLAND_SKM": 2.0}
        self.add(layer, {**base, "HYRIV_ID": 1, "ORD_STRA": 5}, (-100.02, 40.0, -100.01, 40.01))
        self.add(layer, {**base, "HYRIV_ID": 2, "ORD_STRA": 5}, None)
        reaches = vendor_rivers.load_reaches(layer)
        self.assertEqual(sorted(reaches), [1])

    def test_reference_lines_skips_a_null_geometry(self):
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        dataset, layer = self.memory_layer(srs, [("name", ogr.OFTString), ("featurecla", ogr.OFTString)])
        self.add(layer, {"name": "Missouri", "featurecla": "River"}, (-100.02, 40.0, -100.01, 40.01))
        self.add(layer, {"name": "Ghost", "featurecla": "River"}, None)
        self.assertEqual([name for name, _ in vendor_rivers.reference_lines([layer])], ["Missouri"])


class TestReferenceLines(RiverFixtures):
    def test_keeps_rivers_intermittent_rivers_and_lake_centerlines_in_the_box(self):
        lines = vendor_rivers.reference_lines([self.ne_a_layer, self.ne_b_layer])
        self.assertEqual([name for name, _ in lines], ["Missouri", "Lake Sakakawea", "Yellowstone"])
        self.assertEqual(lines[0][1], [[-100.02, 40.0], [-100.01, 40.01]])


class TestBandFeatures(RiverFixtures):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reaches = vendor_rivers.load_reaches(cls.hydro_layer)
        cls.names = {11: "Missouri", 13: "Missouri"}

    def test_order_6_and_above_returns_two_features_ord_6_then_7(self):
        features = vendor_rivers.band_features(self.reaches, self.names, 6, 99)
        self.assertEqual([f["properties"]["ord"] for f in features], [6, 7])
        for feature in features:
            self.assertEqual(feature["geometry"]["type"], "MultiLineString")
        self.assertEqual(len(features[0]["geometry"]["coordinates"]), 2)   # two order-6 reaches
        self.assertEqual(len(features[1]["geometry"]["coordinates"]), 1)   # one order-7 reach

    def test_orders_4_and_5_returns_two_features_ord_4_then_5(self):
        features = vendor_rivers.band_features(self.reaches, self.names, 4, 5)
        self.assertEqual([f["properties"]["ord"] for f in features], [4, 5])
        for feature in features:
            self.assertEqual(len(feature["geometry"]["coordinates"]), 1)

    def test_attribute_arrays_match_the_parts_in_ascending_id_order(self):
        features = vendor_rivers.band_features(self.reaches, self.names, 6, 99)
        six, seven = (f["properties"] for f in features)
        self.assertEqual(six["dis"], [250.0, 120.0])
        self.assertEqual(six["up"], [4322, 2000])
        self.assertEqual(six["name"], ["Missouri", None])
        self.assertEqual(seven, {"ord": 7, "dis": [1234.6], "up": [98765], "name": [None]})
        for feature in features:
            parts = len(feature["geometry"]["coordinates"])
            for key in ("dis", "up", "name"):
                self.assertEqual(len(feature["properties"][key]), parts)

    def test_multilinestring_reach_repeats_its_attributes_per_part(self):
        reaches = {1: {"next": None, "ord": 6, "dis": 5.56, "up": 10.4,
                       "parts": [[[0.0, 0.0], [1.0, 1.0]], [[2.0, 2.0], [3.0, 3.0]]]}}
        (feature,) = vendor_rivers.band_features(reaches, {1: "X"}, 6, 99)
        self.assertEqual(feature["properties"], {"ord": 6, "dis": [5.6, 5.6], "up": [10, 10], "name": ["X", "X"]})
        self.assertEqual(len(feature["geometry"]["coordinates"]), 2)

    def test_order_8_outside_conus_never_appears_in_either_band(self):
        for low, high in vendor_rivers.BANDS.values():
            features = vendor_rivers.band_features(self.reaches, self.names, low, high)
            self.assertNotIn(8, [f["properties"]["ord"] for f in features])

    def test_coordinates_carry_at_most_4_decimals(self):
        features = vendor_rivers.band_features(self.reaches, self.names, 6, 99)
        order_7 = next(f for f in features if f["properties"]["ord"] == 7)
        self.assertIn([-100.1235, 40.6543], order_7["geometry"]["coordinates"][0])
        for feature in features:
            for part in feature["geometry"]["coordinates"]:
                for lon, lat in part:
                    self.assertEqual(round(lon, 4), lon)
                    self.assertEqual(round(lat, 4), lat)


class TestMain(RiverFixtures):
    def test_writes_two_files_and_prints_the_summary_line(self):
        out6 = self.tmp / "out6.geojson"
        out4 = self.tmp / "out4.geojson"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = vendor_rivers.main([str(self.tmp / "hydro.shp"), str(self.tmp / "ne_a.shp"),
                                       str(self.tmp / "ne_b.shp"), str(out6), str(out4)])
        self.assertEqual(code, 0)
        band6 = json.loads(out6.read_text())["features"]
        band4 = json.loads(out4.read_text())["features"]
        self.assertEqual(len(band6), 2)
        self.assertEqual(len(band4), 2)
        self.assertEqual(band6[0]["properties"]["name"], ["Missouri", None])
        named = [n for f in band6 + band4 for n in f["properties"]["name"] if n is not None]
        self.assertEqual(named, ["Missouri", "Missouri"])   # reaches 11 (order 6) and 13 (order 5)
        self.assertEqual(stdout.getvalue().strip(),
                         f"rivers: 3 reaches of order 6+ -> {out6}; 2 of orders 4-5 -> {out4}; "
                         f"2 reaches named from 1 Natural Earth rivers")
        self.assertFalse(list(self.tmp.glob("*.part")))

    def test_wrong_argument_count_returns_2(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = vendor_rivers.main(["one", "two"])
        self.assertEqual(code, 2)
        self.assertIn("usage", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
