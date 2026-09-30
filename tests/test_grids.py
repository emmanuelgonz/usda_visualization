import unittest

from viz import grids


class TestGrids(unittest.TestCase):
    def test_mgrs_ring_is_a_closed_five_vertex_ring(self):
        self.assertEqual(grids.NAMES, ("mgrs",))
        ring = grids.mgrs.ring("T15TVH")
        self.assertEqual(len(ring), 5)
        self.assertEqual(ring[0], ring[-1])
        self.assertTrue(-95 < ring[0][0] < -92 and 40 < ring[0][1] < 43)   # central Iowa
        self.assertIsNone(grids.mgrs.ring("nonsense"))

    def test_unknown_grid_is_a_key_error(self):
        with self.assertRaises(KeyError):
            grids.get("hex")
