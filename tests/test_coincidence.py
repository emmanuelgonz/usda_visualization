import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import coincidence


def emit_feature(fid, lon, lat, start):
    ring = [[lon - 0.4, lat - 0.4], [lon + 0.4, lat - 0.4], [lon + 0.4, lat + 0.4],
            [lon - 0.4, lat + 0.4], [lon - 0.4, lat - 0.4]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": fid, "start": start, "end": start, "cloud": 5.0,
                           "year": int(start[:4]), "browse": None, "data": None}}


def eco_feature(fid, lon, lat, start, daynight="DAY", half=3.0):
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
            [lon - half, lat + half], [lon - half, lat - half]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": fid, "start": start, "end": start, "daynight": daynight,
                           "year": int(start[:4]), "orbit": 1}}


class TestGeometryHelpers(unittest.TestCase):
    def test_ring_bbox(self):
        ring = [[-95, 40], [-94, 40], [-94, 41], [-95, 41], [-95, 40]]
        self.assertEqual(coincidence.ring_bbox(ring), (-95, 40, -94, 41))

    def test_intersects(self):
        a = (-95, 40, -94, 41)
        self.assertTrue(coincidence.intersects(a, (-94.5, 40.5, -93, 42)))
        self.assertTrue(coincidence.intersects(a, (-94, 41, -93, 42)))     # touching corner counts
        self.assertFalse(coincidence.intersects(a, (-93.9, 40, -93, 41)))

    def test_parse_time_handles_fractional_seconds_and_z(self):
        t1 = coincidence.parse_time("2025-04-22T16:47:31.716Z")
        t0 = coincidence.parse_time("2025-04-22T16:47:42.000Z")
        self.assertAlmostEqual(t1 - t0, -10.284, places=3)


class TestPair(unittest.TestCase):
    def setUp(self):
        self.emit = [emit_feature("E1", -93.6, 42.0, "2025-04-22T16:47:42Z")]
        self.eco = [
            eco_feature("same-pass", -93.0, 42.0, "2025-04-22T16:47:31Z"),          # dt -11 s, overlaps
            eco_feature("three-hours", -93.0, 42.0, "2025-04-22T19:47:42Z"),        # dt +3 h, overlaps
            eco_feature("thirty-hours", -93.0, 42.0, "2025-04-23T22:47:42Z"),       # dt +30 h, excluded
            eco_feature("elsewhere", -80.0, 30.0, "2025-04-22T16:47:50Z"),         # near in time, no overlap
            eco_feature("night", -93.0, 42.0, "2025-04-22T04:00:00Z", "NIGHT"),    # dt -12.8 h, overlaps
        ]

    def test_pairs_are_nearest_first_with_signed_dt_and_exclusions(self):
        coincidence.pair(self.emit, self.eco)
        pairs = self.emit[0]["properties"]["eco"]
        self.assertEqual([p["id"] for p in pairs], ["same-pass", "three-hours", "night"])
        self.assertEqual(pairs[0]["dt"], -11)
        self.assertEqual(pairs[1]["dt"], 3 * 3600)
        self.assertEqual(pairs[2]["daynight"], "NIGHT")
        self.assertEqual(set(pairs[0]), {"id", "start", "daynight", "dt"})

    def test_returns_count_within_fifteen_minutes(self):
        self.assertEqual(coincidence.pair(self.emit, self.eco), 1)
        far = [emit_feature("E2", -100.0, 35.0, "2025-04-22T16:47:42Z")]
        self.assertEqual(coincidence.pair(far, self.eco), 0)
        self.assertEqual(far[0]["properties"]["eco"], [])

    def test_cap(self):
        many = [eco_feature(f"e{i}", -93.0, 42.0, f"2025-04-22T16:{i:02d}:00Z") for i in range(30)]
        coincidence.pair(self.emit, many, cap=20)
        self.assertEqual(len(self.emit[0]["properties"]["eco"]), 20)

    def test_unsorted_input_is_handled(self):
        coincidence.pair(self.emit, list(reversed(self.eco)))
        self.assertEqual(self.emit[0]["properties"]["eco"][0]["id"], "same-pass")


class TestMain(unittest.TestCase):
    def test_rewrites_emit_file_with_pairs(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        from viz import paths
        saved = (paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS)
        paths.EMIT_FOOTPRINTS = tmp / "emit.geojson"
        paths.ECO_FOOTPRINTS = tmp / "eco.geojson"
        try:
            paths.EMIT_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": [
                emit_feature("E1", -93.6, 42.0, "2025-04-22T16:47:42Z")]}))
            paths.ECO_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": [
                eco_feature("same-pass", -93.0, 42.0, "2025-04-22T16:47:31Z")]}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(coincidence.main([]), 0)
            out = json.loads(paths.EMIT_FOOTPRINTS.read_text())
            self.assertEqual(out["features"][0]["properties"]["eco"][0]["id"], "same-pass")
            self.assertFalse(list(tmp.glob("*.part")))
        finally:
            paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS = saved

    def test_main_without_eco_file_returns_1(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        from viz import paths
        saved = (paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS)
        paths.EMIT_FOOTPRINTS = tmp / "emit.geojson"
        paths.ECO_FOOTPRINTS = tmp / "absent.geojson"
        try:
            paths.EMIT_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(coincidence.main([]), 1)
        finally:
            paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS = saved


if __name__ == "__main__":
    unittest.main()
