import json
import shutil
import tempfile
import unittest
from pathlib import Path

from osgeo import ogr, osr

from viz import vendor_states


def _square(x0, y0, size):
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size), (x0, y0)):
        ring.AddPoint(x, y)
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    return poly


class TestLabelPoint(unittest.TestCase):
    def test_multipolygon_label_lands_on_the_largest_part(self):
        # A Michigan-like shape: a small northern part and a large southern part.
        multi = ogr.Geometry(ogr.wkbMultiPolygon)
        multi.AddGeometry(_square(0, 10, 1))   # small, far north
        multi.AddGeometry(_square(0, 0, 5))    # large, south
        lon, lat = vendor_states.label_point(multi)
        self.assertTrue(0 <= lon <= 5 and 0 <= lat <= 5, (lon, lat))

    def test_single_polygon_label_is_inside_it(self):
        lon, lat = vendor_states.label_point(_square(-95, 40, 3))
        self.assertTrue(-95 <= lon <= -92 and 40 <= lat <= 43, (lon, lat))


class TestExcludedStates(unittest.TestCase):
    def test_non_conus_fips_are_listed(self):
        for fips in ("02", "15", "60", "66", "69", "72", "78"):
            self.assertIn(fips, vendor_states.EXCLUDED_STATEFP)
        self.assertNotIn("19", vendor_states.EXCLUDED_STATEFP)  # Iowa


class TestConvert(unittest.TestCase):
    def test_writes_conus_geojson_with_label_fields(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        src = tmp / "states.shp"
        srs = osr.SpatialReference(); srs.ImportFromEPSG(4269)
        driver = ogr.GetDriverByName("ESRI Shapefile")
        ds = driver.CreateDataSource(str(src))
        layer = ds.CreateLayer("states", srs, ogr.wkbMultiPolygon)
        for name in ("STATEFP", "STUSPS", "NAME"):
            layer.CreateField(ogr.FieldDefn(name, ogr.OFTString))
        for fips, abbr, name, x in (("19", "IA", "Iowa", -95), ("02", "AK", "Alaska", -150)):
            feat = ogr.Feature(layer.GetLayerDefn())
            feat.SetField("STATEFP", fips); feat.SetField("STUSPS", abbr); feat.SetField("NAME", name)
            feat.SetGeometry(_square(x, 40, 2)); layer.CreateFeature(feat)
        ds = None

        out = tmp / "states.geojson"
        count = vendor_states.convert(str(src), str(out))
        data = json.loads(out.read_text())
        self.assertEqual(count, 1)
        self.assertEqual(len(data["features"]), 1)
        props = data["features"][0]["properties"]
        self.assertEqual(props["STUSPS"], "IA")
        for key in ("NAME", "STUSPS", "INTPTLAT", "INTPTLON"):
            self.assertIn(key, props)
        self.assertTrue(-95 <= props["INTPTLON"] <= -93)


if __name__ == "__main__":
    unittest.main()
