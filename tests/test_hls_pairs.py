import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import hls, hls_pairs

SQUARE = [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 43.0], [-94.0, 42.0]]


def emit_feature(fid, lon, lat, start):
    ring = [[lon - 0.4, lat - 0.4], [lon + 0.4, lat - 0.4], [lon + 0.4, lat + 0.4],
            [lon - 0.4, lat + 0.4], [lon - 0.4, lat - 0.4]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": fid, "start": start, "end": start, "cloud": 5.0,
                           "year": int(start[:4]), "browse": None, "data": None}}


def acq(ur, start, cloud):
    sensor, tile = hls.parse_ur(ur)
    return {"id": ur, "tile": tile, "date": start[:10], "time": start, "sensor": sensor, "cloud": cloud}


class TestCentroid(unittest.TestCase):
    def test_ignores_the_closing_vertex(self):
        self.assertEqual(hls_pairs.centroid(SQUARE), (-93.5, 42.5))
        self.assertEqual(hls_pairs.centroid(SQUARE[:-1]), (-93.5, 42.5))


class TestPair(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = hls.Store(self.tmp / "hls.sqlite")
        self.addCleanup(self.store.close)
        rows = [
            acq("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10),
            acq("HLS.L30.T15TVH.2025202T160000.v2.0", "2025-07-21T16:00:00Z", 80),
            acq("HLS.S30.T15TVH.2025204T170000.v2.0", "2025-07-23T17:00:00Z", None),
            acq("HLS.S30.T15TVH.2025190T170000.v2.0", "2025-07-09T17:00:00Z", 0),    # 11 days before
            acq("HLS.S30.T15TVH.2025180T170000.v2.0", "2025-06-29T17:00:00Z", 0),    # 21 days before
            acq("HLS.S30.T15TVH.2025225T170000.v2.0", "2025-08-13T17:00:00Z", 0),    # 24 days after
        ]
        fetched = "2025-09-01T00:00:00+00:00"
        s30 = [r for r in rows if r["sensor"] == "S30"]
        self.store.replace_month("S30", "2025-06", [r for r in s30 if r["date"] < "2025-07-01"], fetched)
        self.store.replace_month("S30", "2025-07", [r for r in s30 if "2025-07-01" <= r["date"] < "2025-08-01"], fetched)
        self.store.replace_month("S30", "2025-08", [r for r in s30 if r["date"] >= "2025-08-01"], fetched)
        self.store.replace_month("L30", "2025-07", [r for r in rows if r["sensor"] == "L30"], fetched)
        self.store.put_tile("T15TVH", SQUARE)
        self.index = hls.TileIndex(self.store)

    def test_writes_nearest_first_within_the_span_and_keeps_unknown_cloud(self):
        feature = emit_feature("inside", -93.5, 42.5, "2025-07-20T18:00:00Z")
        paired = hls_pairs.pair([feature], self.store, self.index)
        self.assertEqual(paired, 1)
        got = feature["properties"]["hls_near"]
        self.assertEqual([(h["date"], h["sensor"], h["cloud"], h["dt"]) for h in got], [
            ("2025-07-21", "L30", 80, 1),
            ("2025-07-18", "S30", 10, -2),
            ("2025-07-23", "S30", None, 3),
            ("2025-07-09", "S30", 0, -11),
        ])

    def test_overlapping_tiles_merge_with_one_entry_per_date_and_sensor(self):
        # T15TWH overlaps T15TVH's eastern strip; it adds a date of its own and a
        # cloudier copy of an existing pass, which must not duplicate the entry.
        self.store.put_tile("T15TWH", [[-93.2, 42.0], [-92.2, 42.0], [-92.2, 43.0], [-93.2, 43.0], [-93.2, 42.0]])
        self.store.replace_month("L30", "2025-07", [
            acq("HLS.L30.T15TVH.2025202T160000.v2.0", "2025-07-21T16:00:00Z", 80),
            acq("HLS.L30.T15TWH.2025202T160100.v2.0", "2025-07-21T16:01:00Z", 90),   # same pass, cloudier
            acq("HLS.L30.T15TWH.2025200T160000.v2.0", "2025-07-19T16:00:00Z", 5),    # only the neighbour saw it
        ], "2025-09-01T00:00:00+00:00")
        index = hls.TileIndex(self.store)
        feature = emit_feature("overlap", -93.1, 42.5, "2025-07-20T18:00:00Z")
        self.assertEqual(index.covering(-93.1, 42.5), ["T15TVH", "T15TWH"])
        hls_pairs.pair([feature], self.store, index)
        got = feature["properties"]["hls_near"]
        self.assertEqual([(h["date"], h["sensor"], h["cloud"], h["dt"]) for h in got], [
            ("2025-07-19", "L30", 5, -1),
            ("2025-07-21", "L30", 80, 1),
            ("2025-07-18", "S30", 10, -2),
            ("2025-07-23", "S30", None, 3),
            ("2025-07-09", "S30", 0, -11),
        ])

    def test_scene_outside_every_tile_gets_an_empty_list(self):
        feature = emit_feature("outside", -80.0, 42.5, "2025-07-20T18:00:00Z")
        self.assertEqual(hls_pairs.pair([feature], self.store, self.index), 0)
        self.assertEqual(feature["properties"]["hls_near"], [])

    def test_cap_and_span_are_parameters(self):
        feature = emit_feature("inside", -93.5, 42.5, "2025-07-20T18:00:00Z")
        hls_pairs.pair([feature], self.store, self.index, days=30, cap=2)
        self.assertEqual([h["dt"] for h in feature["properties"]["hls_near"]], [1, -2])
        hls_pairs.pair([feature], self.store, self.index, days=30, cap=20)
        self.assertEqual([h["dt"] for h in feature["properties"]["hls_near"]], [1, -2, 3, -11, -21, 24])

    def test_main_rewrites_the_emit_file(self):
        from unittest import mock
        emit_path = self.tmp / "emit.geojson"
        emit_path.write_text(json.dumps({"type": "FeatureCollection", "features": [
            emit_feature("inside", -93.5, 42.5, "2025-07-20T18:00:00Z"),
        ]}))
        import contextlib
        import io
        with mock.patch.object(hls_pairs.paths, "EMIT_FOOTPRINTS", emit_path), \
             mock.patch.object(hls_pairs.paths, "HLS_DB", self.store.path), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hls_pairs.main([]), 0)
        doc = json.loads(emit_path.read_text())
        self.assertEqual(len(doc["features"][0]["properties"]["hls_near"]), 4)

    def test_main_reports_a_missing_store(self):
        from unittest import mock
        import contextlib
        import io
        err = io.StringIO()
        with mock.patch.object(hls_pairs.paths, "HLS_DB", self.tmp / "nope.sqlite"), \
             contextlib.redirect_stderr(err):
            self.assertEqual(hls_pairs.main([]), 1)
        self.assertIn("run.sh hls", err.getvalue())


if __name__ == "__main__":
    unittest.main()
