import math
import unittest

from viz import gridmath

HALF = 20037508.342789244


class TestTileBounds(unittest.TestCase):
    def test_zoom_zero_covers_the_whole_world(self):
        minx, miny, maxx, maxy = gridmath.tile_bounds(0, 0, 0)
        self.assertAlmostEqual(minx, -HALF, places=6)
        self.assertAlmostEqual(miny, -HALF, places=6)
        self.assertAlmostEqual(maxx, HALF, places=6)
        self.assertAlmostEqual(maxy, HALF, places=6)

    def test_zoom_one_quadrants_meet_at_the_origin(self):
        top_left = gridmath.tile_bounds(1, 0, 0)
        bottom_right = gridmath.tile_bounds(1, 1, 1)
        self.assertAlmostEqual(top_left[2], 0.0, places=6)   # maxx
        self.assertAlmostEqual(top_left[1], 0.0, places=6)   # miny
        self.assertAlmostEqual(bottom_right[0], 0.0, places=6)
        self.assertAlmostEqual(bottom_right[3], 0.0, places=6)

    def test_y_increases_downward(self):
        upper = gridmath.tile_bounds(3, 4, 2)
        lower = gridmath.tile_bounds(3, 4, 3)
        self.assertGreater(upper[1], lower[1])

    def test_tiles_are_square_at_every_zoom(self):
        for z in range(0, 15):
            minx, miny, maxx, maxy = gridmath.tile_bounds(z, 0, 0)
            with self.subTest(z=z):
                self.assertAlmostEqual(maxx - minx, maxy - miny, places=6)

    def test_adjacent_tiles_share_an_edge_without_gap_or_overlap(self):
        left = gridmath.tile_bounds(6, 10, 20)
        right = gridmath.tile_bounds(6, 11, 20)
        self.assertAlmostEqual(left[2], right[0], places=9)


class TestLonLatToTile(unittest.TestCase):
    def test_null_island_at_zoom_one(self):
        self.assertEqual(gridmath.lonlat_to_tile(0.0001, 0.0001, 1), (1, 0))

    def test_ames_iowa_matches_slippy_map_reference(self):
        # Reference values from the standard slippy map formula.
        self.assertEqual(gridmath.lonlat_to_tile(-93.62, 42.03, 7), (30, 47))
        self.assertEqual(gridmath.lonlat_to_tile(-93.62, 42.03, 10), (245, 380))

    def test_round_trips_into_the_tile_it_names(self):
        lon, lat, z = -93.62, 42.03, 9
        x, y = gridmath.lonlat_to_tile(lon, lat, z)
        minx, miny, maxx, maxy = gridmath.tile_bounds(z, x, y)
        mx = HALF * lon / 180.0
        my = HALF * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi
        self.assertTrue(minx <= mx <= maxx)
        self.assertTrue(miny <= my <= maxy)


class TestCpcGrid(unittest.TestCase):
    def test_matches_the_canonical_grid_in_the_spec(self):
        self.assertEqual(gridmath.CPC_GRID["width"], 508)
        self.assertEqual(gridmath.CPC_GRID["height"], 320)
        self.assertAlmostEqual(gridmath.CPC_GRID["origin_x"], -2309800.213402342982590, places=6)
        self.assertAlmostEqual(gridmath.CPC_GRID["origin_y"], 3185470.286793155129999, places=6)
        self.assertAlmostEqual(gridmath.CPC_GRID["pixel_x"], 8999.255456289853100, places=6)
        self.assertAlmostEqual(gridmath.CPC_GRID["pixel_y"], -8995.486488541766448, places=6)
        self.assertEqual(gridmath.CPC_GRID["srs"], "EPSG:5070")

    def test_bounds_are_ordered_and_span_the_grid(self):
        minx, miny, maxx, maxy = gridmath.cpc_grid_bounds()
        self.assertLess(minx, maxx)
        self.assertLess(miny, maxy)
        self.assertAlmostEqual(maxx - minx, 508 * 8999.255456289853100, places=3)
        self.assertAlmostEqual(maxy - miny, 320 * 8995.486488541766448, places=3)


if __name__ == "__main__":
    unittest.main()
