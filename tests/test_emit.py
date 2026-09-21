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


if __name__ == "__main__":
    unittest.main()
