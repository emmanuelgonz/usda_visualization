import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from viz import basins

OUTER = [[-100, 40], [-98, 40], [-98, 42], [-100, 42], [-100, 40]]
HOLE = [[-99.5, 40.5], [-98.5, 40.5], [-98.5, 41.5], [-99.5, 41.5], [-99.5, 40.5]]
ISLAND = [[-97, 40], [-96, 40], [-96, 41], [-97, 41], [-97, 40]]


def collection():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"huc": "10", "name": "Missouri Region", "label_lon": -99.8, "label_lat": 40.2},
         "geometry": {"type": "MultiPolygon", "coordinates": [[OUTER, HOLE], [ISLAND]]}},
        {"type": "Feature", "properties": {"huc": "11", "name": "Arkansas-White-Red Region", "label_lon": -99, "label_lat": 38.5},
         "geometry": {"type": "Polygon", "coordinates": [[[-100, 38], [-98, 38], [-98, 40], [-100, 40], [-100, 38]]]}},
    ]}


class TestPointInPolygon(unittest.TestCase):
    def test_outer_hole_and_outside(self):
        self.assertTrue(basins.point_in_polygon(-99.8, 40.2, [OUTER, HOLE]))
        self.assertFalse(basins.point_in_polygon(-99.0, 41.0, [OUTER, HOLE]))    # inside the hole
        self.assertFalse(basins.point_in_polygon(-95.0, 41.0, [OUTER, HOLE]))
        self.assertFalse(basins.point_in_polygon(-99.0, 41.0, []))


class TestBasinIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "basins2.geojson"
        self.path.write_text(json.dumps(collection()))

    def test_covering_handles_multipolygons_holes_and_islands(self):
        index = basins.BasinIndex(self.path)
        self.assertEqual(index.count, 2)
        self.assertEqual(index.covering(-99.8, 40.2), {"huc": "10", "name": "Missouri Region"})
        self.assertIsNone(index.covering(-99.0, 41.0))                              # the hole
        self.assertEqual(index.covering(-96.5, 40.5)["huc"], "10")                 # the island part
        self.assertEqual(index.covering(-99.0, 39.0)["huc"], "11")
        self.assertIsNone(index.covering(-90.0, 39.0))

    def test_index_for_caches_by_path_and_rebuilds_on_change(self):
        self.assertIsNone(basins.index_for(self.tmp / "nope.geojson"))
        first = basins.index_for(self.path)
        self.assertIs(basins.index_for(self.path), first)
        data = collection()
        data["features"] = data["features"][:1]
        self.path.write_text(json.dumps(data))
        os.utime(self.path, ns=(time.time_ns() + 10 ** 9, time.time_ns() + 10 ** 9))
        second = basins.index_for(self.path)
        self.assertIsNot(second, first)
        self.assertEqual(second.count, 1)


if __name__ == "__main__":
    unittest.main()
