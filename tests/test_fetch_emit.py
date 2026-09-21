import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import fetch_emit

ENTRY = {
    "title": "EMIT_L2A_RFL_001_20250124T204129_2502414_001",
    "time_start": "2025-01-24T20:41:29.000Z",
    "time_end": "2025-01-24T20:41:41.000Z",
    "cloud_cover": "3",
    "polygons": [["33.0510864 -107.7900085 32.4306107 -108.5158539 31.8290176 -108.0015869 "
                  "32.4494934 -107.2757416 33.0510864 -107.7900085"]],
    "links": [
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://data.example/x.nc"},
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/browse#", "href": "https://data.example/x.png"},
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/metadata#", "href": "https://data.example/x.xml"},
    ],
}


class TestEntryToFeature(unittest.TestCase):
    def test_swaps_lat_lon_to_geojson_order(self):
        feature = fetch_emit.entry_to_feature(ENTRY)
        ring = feature["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], [-107.7900085, 33.0510864])
        self.assertEqual(ring[0], ring[-1])  # closed

    def test_properties(self):
        props = fetch_emit.entry_to_feature(ENTRY)["properties"]
        self.assertEqual(props["id"], "EMIT_L2A_RFL_001_20250124T204129_2502414_001")
        self.assertEqual(props["start"], "2025-01-24T20:41:29.000Z")
        self.assertEqual(props["end"], "2025-01-24T20:41:41.000Z")
        self.assertEqual(props["cloud"], 3.0)
        self.assertEqual(props["year"], 2025)
        self.assertEqual(props["browse"], "https://data.example/x.png")
        self.assertEqual(props["data"], "https://data.example/x.nc")

    def test_missing_browse_link_is_none(self):
        entry = dict(ENTRY, links=[ENTRY["links"][0]])
        self.assertIsNone(fetch_emit.entry_to_feature(entry)["properties"]["browse"])

    def test_missing_cloud_is_none(self):
        entry = dict(ENTRY); del entry["cloud_cover"]
        self.assertIsNone(fetch_emit.entry_to_feature(entry)["properties"]["cloud"])

    def test_entry_without_polygon_is_skipped(self):
        entry = dict(ENTRY); del entry["polygons"]
        self.assertIsNone(fetch_emit.entry_to_feature(entry))


class TestFetchAll(unittest.TestCase):
    def test_pages_until_an_empty_page(self):
        pages = {1: [ENTRY, ENTRY], 2: [ENTRY], 3: []}
        asked = []

        def fake(page_num):
            asked.append(page_num)
            return pages.get(page_num, [])

        entries = fetch_emit.fetch_all(fake)
        self.assertEqual(len(entries), 3)
        self.assertEqual(asked, [1, 2, 3])


class TestWriteGeojson(unittest.TestCase):
    def test_writes_a_feature_collection_atomically(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        out = tmp / "emit" / "footprints.geojson"
        fetch_emit.write_geojson([fetch_emit.entry_to_feature(ENTRY)], out)
        data = json.loads(out.read_text())
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(len(data["features"]), 1)
        self.assertFalse(list(tmp.glob("**/*.part")))


if __name__ == "__main__":
    unittest.main()
