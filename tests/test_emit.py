import datetime
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from viz import emit


class TestWeekSunday(unittest.TestCase):
    def test_anchor_from_the_archive_timestamps(self):
        # cornCond24w15.tif was written Monday 2024-04-15, the day NASS
        # publishes the report for the week ending Sunday 2024-04-14.
        self.assertEqual(emit.week_sunday(2024, 15), datetime.date(2024, 4, 14))

    def test_week_one_and_week_fifty_two(self):
        self.assertEqual(emit.week_sunday(2025, 1), datetime.date(2025, 1, 5))
        self.assertEqual(emit.week_sunday(2025, 52), datetime.date(2025, 12, 28))

    def test_result_is_always_a_sunday(self):
        for year in (2015, 2020, 2026):
            for week in (1, 15, 30, 46):
                self.assertEqual(emit.week_sunday(year, week).isoweekday(), 7)


SQUARE = [[-95.0, 40.0], [-94.0, 40.0], [-94.0, 41.0], [-95.0, 41.0], [-95.0, 40.0]]
# A concave "C" whose bounding box contains a point the ring does not.
CEE = [[-100.0, 30.0], [-98.0, 30.0], [-98.0, 30.5], [-99.5, 30.5], [-99.5, 31.5],
       [-98.0, 31.5], [-98.0, 32.0], [-100.0, 32.0], [-100.0, 30.0]]


class TestPointInRing(unittest.TestCase):
    def test_inside(self):
        self.assertTrue(emit.point_in_ring(-94.5, 40.5, SQUARE))

    def test_outside(self):
        self.assertFalse(emit.point_in_ring(-93.0, 40.5, SQUARE))

    def test_inside_bbox_but_outside_concave_ring(self):
        self.assertFalse(emit.point_in_ring(-98.5, 31.0, CEE))   # the notch of the C
        self.assertTrue(emit.point_in_ring(-99.75, 31.0, CEE))   # the spine of the C

    def test_unclosed_ring_works_too(self):
        self.assertTrue(emit.point_in_ring(-94.5, 40.5, SQUARE[:-1]))


def _feature(fid, ring, start, cloud=10.0):
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"id": fid, "start": start, "end": start, "cloud": cloud,
                       "year": int(start[:4]), "browse": None, "data": None},
    }


class TestFootprintIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = self.tmp / "footprints.geojson"
        far = [[-80.0, 30.0], [-79.0, 30.0], [-79.0, 31.0], [-80.0, 31.0], [-80.0, 30.0]]
        self.path.write_text(json.dumps({"type": "FeatureCollection", "features": [
            _feature("old", SQUARE, "2023-06-01T12:00:00Z"),
            _feature("new", SQUARE, "2025-06-01T12:00:00Z"),
            _feature("far", far, "2024-06-01T12:00:00Z"),
        ]}))

    def test_count_and_fetched(self):
        index = emit.FootprintIndex(self.path)
        self.assertEqual(index.count, 3)
        self.assertRegex(index.fetched, r"^\d{4}-\d{2}-\d{2}T")

    def test_covering_returns_matches_newest_first(self):
        index = emit.FootprintIndex(self.path)
        ids = [g["id"] for g in index.covering(-94.5, 40.5)]
        self.assertEqual(ids, ["new", "old"])

    def test_covering_outside_everything_is_empty(self):
        self.assertEqual(emit.FootprintIndex(self.path).covering(0.0, 0.0), [])

    def test_index_for_caches_by_path_and_mtime(self):
        first = emit.index_for(self.path)
        self.assertIs(emit.index_for(self.path), first)
        time.sleep(0.05)
        self.path.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
        import os
        os.utime(self.path, None)
        second = emit.index_for(self.path)
        self.assertIsNot(second, first)
        self.assertEqual(second.count, 0)

    def test_index_for_missing_file_is_none(self):
        self.assertIsNone(emit.index_for(self.tmp / "absent.geojson"))

    def test_index_for_keeps_two_files_cached_independently(self):
        other = self.tmp / "other.geojson"
        other.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
        a1 = emit.index_for(self.path)
        b1 = emit.index_for(other)
        a2 = emit.index_for(self.path)
        b2 = emit.index_for(other)
        self.assertIs(a1, a2)
        self.assertIs(b1, b2)
        self.assertIsNot(a1, b1)

    def test_count_where(self):
        index = emit.FootprintIndex(self.path)
        self.assertEqual(index.count_where(lambda p: p["id"].startswith("n")), 1)
        self.assertEqual(index.count_where(lambda p: True), 3)


SF_RING = [[-121.662, 38.761], [-122.56, 38.167], [-122.119, 37.501], [-121.221, 38.095], [-121.662, 38.761]]
SF_NEXT = [[-121.0, 39.3], [-121.9, 38.7], [-121.46, 38.04], [-120.56, 38.63], [-121.0, 39.3]]      # further north-east
MIA_RING = [[-79.9, 26.04], [-80.5, 25.5], [-79.95, 24.9], [-79.35, 25.44], [-79.9, 26.04]]
MIA_PREV = [[-80.5, 26.7], [-81.1, 26.16], [-80.55, 25.56], [-79.95, 26.1], [-80.5, 26.7]]           # further north-west


def scene_feature(fid, ring, start="2024-07-30T20:39:50Z"):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": fid, "start": start, "end": start, "cloud": 10.0, "year": 2024,
                           "browse": "https://data.lpdaac.earthdatacloud.nasa.gov/x/" + fid + ".png", "data": None}}


class TestOrbitKey(unittest.TestCase):
    def test_parses_orbit_and_scene_number(self):
        self.assertEqual(emit.orbit_key("EMIT_L2A_RFL_001_20240730T203950_2421214_004"), ("2421214", 4))

    def test_malformed_is_none(self):
        self.assertIsNone(emit.orbit_key("near-new"))
        self.assertIsNone(emit.orbit_key("EMIT_L2A_RFL_001_20240730T203950_2421214"))
        self.assertIsNone(emit.orbit_key(None))


class TestSceneCorners(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        path = self.tmp / "footprints.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": [
            scene_feature("EMIT_L2A_RFL_001_20240730T203950_2421214_004", SF_RING),
            scene_feature("EMIT_L2A_RFL_001_20240730T204002_2421214_005", SF_NEXT),
            scene_feature("EMIT_L2A_RFL_001_20240622T165755_2417411_055", MIA_RING),
            scene_feature("EMIT_L2A_RFL_001_20240622T165743_2417411_054", MIA_PREV),
            scene_feature("EMIT_L2A_RFL_001_20240101T000000_2400101_001", SF_RING),   # no neighbour
            scene_feature("near-new", SF_RING),                                        # no orbit key
        ]}))
        self.index = emit.FootprintIndex(path)

    def test_scene_lookup_and_bbox(self):
        self.assertEqual(self.index.scene("near-new")["id"], "near-new")
        self.assertIsNone(self.index.scene("nope"))
        self.assertEqual(self.index.scene_bbox("EMIT_L2A_RFL_001_20240622T165755_2417411_055"),
                         (-80.5, 24.9, -79.35, 26.04))
        self.assertIsNone(self.index.scene_bbox("nope"))

    def test_neighbour_prefers_next_then_previous(self):
        props, sign = self.index.neighbour("EMIT_L2A_RFL_001_20240730T203950_2421214_004")
        self.assertEqual((props["id"][-3:], sign), ("005", 1))
        props, sign = self.index.neighbour("EMIT_L2A_RFL_001_20240622T165755_2417411_055")
        self.assertEqual((props["id"][-3:], sign), ("054", -1))
        self.assertIsNone(self.index.neighbour("EMIT_L2A_RFL_001_20240101T000000_2400101_001"))
        self.assertIsNone(self.index.neighbour("near-new"))

    def test_ascending_pass_corner_order(self):
        corners = emit.scene_corners(self.index, "EMIT_L2A_RFL_001_20240730T203950_2421214_004")
        self.assertEqual(corners, [SF_RING[2], SF_RING[1], SF_RING[0], SF_RING[3]])

    def test_descending_pass_corner_order(self):
        corners = emit.scene_corners(self.index, "EMIT_L2A_RFL_001_20240622T165755_2417411_055")
        self.assertEqual(corners, [MIA_RING[1], MIA_RING[0], MIA_RING[3], MIA_RING[2]])

    def test_unorientable_scenes_are_none(self):
        self.assertIsNone(emit.scene_corners(self.index, "EMIT_L2A_RFL_001_20240101T000000_2400101_001"))
        self.assertIsNone(emit.scene_corners(self.index, "near-new"))
        self.assertIsNone(emit.scene_corners(self.index, "nope"))


if __name__ == "__main__":
    unittest.main()
