import json
import unittest

from viz import fetch_eco

ENTRY = {
    "title": "ECOv002_L2_LSTE_39898_003_20250720T002641_0713_01",
    "time_start": "2025-07-20T00:26:41.997Z",
    "time_end": "2025-07-20T00:27:33.966Z",
    "day_night_flag": "DAY",
    "boxes": ["25.5522193 -89.8633255 30.4788793 -84.1269103"],
    "orbit_calculated_spatial_domains": [{"start_orbit_number": "39898", "stop_orbit_number": "39898"}],
    "links": [],
}


class TestBoxToRing(unittest.TestCase):
    def test_rectangle_in_lon_lat_order_closed(self):
        ring = fetch_eco.box_to_ring("25.5 -89.9 30.5 -84.1")
        self.assertEqual(ring[0], [-89.9, 25.5])   # south-west
        self.assertEqual(ring[1], [-84.1, 25.5])   # south-east
        self.assertEqual(ring[2], [-84.1, 30.5])   # north-east
        self.assertEqual(ring[3], [-89.9, 30.5])   # north-west
        self.assertEqual(ring[4], ring[0])
        self.assertEqual(len(ring), 5)


class TestEntryToFeature(unittest.TestCase):
    def test_properties(self):
        props = fetch_eco.entry_to_feature(ENTRY)["properties"]
        self.assertEqual(props["id"], ENTRY["title"])
        self.assertEqual(props["start"], "2025-07-20T00:26:41.997Z")
        self.assertEqual(props["end"], "2025-07-20T00:27:33.966Z")
        self.assertEqual(props["daynight"], "DAY")
        self.assertEqual(props["year"], 2025)
        self.assertEqual(props["orbit"], 39898)
        self.assertNotIn("cloud", props)
        self.assertNotIn("browse", props)

    def test_geometry_is_the_box_rectangle(self):
        ring = fetch_eco.entry_to_feature(ENTRY)["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], [-89.8633255, 25.5522193])

    def test_entry_without_box_is_skipped(self):
        entry = dict(ENTRY); del entry["boxes"]
        self.assertIsNone(fetch_eco.entry_to_feature(entry))

    def test_missing_orbit_is_none(self):
        entry = dict(ENTRY); del entry["orbit_calculated_spatial_domains"]
        self.assertIsNone(fetch_eco.entry_to_feature(entry)["properties"]["orbit"])

    def test_unknown_daynight_is_kept_verbatim(self):
        entry = dict(ENTRY, day_night_flag="BOTH")
        self.assertEqual(fetch_eco.entry_to_feature(entry)["properties"]["daynight"], "BOTH")


class TestQuery(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(fetch_eco.SHORT_NAME, "ECO_L2_LSTE")
        self.assertEqual(fetch_eco.VERSION, "002")
        self.assertEqual(fetch_eco.TEMPORAL, "2022-01-01T00:00:00Z,")


if __name__ == "__main__":
    unittest.main()
