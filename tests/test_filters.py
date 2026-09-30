import unittest

from viz import filters, registry

REG = registry.parse({
    "region": {"name": "T", "bbox": [-125.0, 24.4, -66.9, 49.4]},
    "missions": [{
        "key": "hls", "name": "HLS", "label": "HLS coverage", "archetype": "tiled", "grid": "mgrs",
        "cmr": [{"short_name": "HLSL30", "version": "2.0", "implies": {"sensor": "L30"}}],
        "since": "2022-01",
        "tile_from": {"field": "title", "pattern": "^HLS\\.[LS]30\\.(T[0-9]{2}[A-Z]{3})\\."},
        "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}, "sensor": {"from": "sensor", "type": "text"}},
        "filters": [{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud"},
                    {"attribute": "sensor", "control": "choice", "values": ["ALL", "L30", "S30"], "default": "ALL", "label": "Sensor"}],
        "browse": {"source": "links", "match": "\\.jpg$"}, "links": {}, "style": {}}]})
HLS = REG.mission("hls")


class TestParse(unittest.TestCase):
    def test_defaults_and_given_values(self):
        self.assertEqual(filters.parse(HLS, {}), {"cloud": 30, "sensor": "ALL"})
        self.assertEqual(filters.parse(HLS, {"f.cloud": ["55"], "f.sensor": ["S30"]}), {"cloud": 55, "sensor": "S30"})
        self.assertEqual(filters.parse(HLS, {"cloud": ["10"], "sensor": ["L30"]}, prefix=""), {"cloud": 10, "sensor": "L30"})

    def test_bad_filter_values(self):
        for query, word in (({"f.cloud": ["abc"]}, "cloud"), ({"f.cloud": ["-5"]}, "cloud"), ({"f.cloud": ["150"]}, "cloud"),
                            ({"f.sensor": ["X30"]}, "sensor"), ({"f.nope": ["1"]}, "nope")):
            with self.assertRaisesRegex(filters.FilterError, word):
                filters.parse(HLS, query)

    def test_passes(self):
        values = {"cloud": 30, "sensor": "ALL"}
        self.assertTrue(filters.passes({"cloud": 30, "sensor": "S30"}, HLS, values))
        self.assertFalse(filters.passes({"cloud": 31, "sensor": "S30"}, HLS, values))
        self.assertFalse(filters.passes({"cloud": None, "sensor": "S30"}, HLS, values))
        self.assertFalse(filters.passes({"cloud": 5, "sensor": "S30"}, HLS, {"cloud": 30, "sensor": "L30"}))
        self.assertTrue(filters.passes({"cloud": 5, "sensor": "L30"}, HLS, {"cloud": 30, "sensor": "L30"}))

    def test_describe_is_json_able(self):
        described = filters.describe(HLS)
        self.assertEqual(described[0], {"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud", "values": []})
        self.assertEqual(described[1]["values"], ["ALL", "L30", "S30"])
