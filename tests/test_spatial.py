import unittest

from viz import emit, spatial

SQUARE = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
OFFSET = [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]            # overlaps SQUARE; its corner (1, 1) lies inside
FAR = [[10, 10], [11, 10], [11, 11], [10, 11], [10, 10]]
INSIDE = [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5], [0.5, 0.5]]


class TestPrimitives(unittest.TestCase):
    def test_point_in_ring_is_the_emit_function(self):
        self.assertIs(emit.point_in_ring, spatial.point_in_ring)
        self.assertTrue(spatial.point_in_ring(1, 1, SQUARE))
        self.assertFalse(spatial.point_in_ring(3, 1, SQUARE))

    def test_bbox_and_box_intersection(self):
        self.assertEqual(spatial.ring_bbox(OFFSET), (1, 1, 3, 3))
        self.assertTrue(spatial.boxes_intersect((0, 0, 2, 2), (2, 2, 3, 3)))     # touching corner counts
        self.assertFalse(spatial.boxes_intersect((0, 0, 2, 2), (2.1, 0, 3, 2)))

    def test_rings_intersect_by_vertex_containment_and_edge_crossing(self):
        self.assertTrue(spatial.rings_intersect(SQUARE, INSIDE))       # containment
        self.assertTrue(spatial.rings_intersect(INSIDE, SQUARE))
        self.assertTrue(spatial.rings_intersect(SQUARE, OFFSET))       # a vertex of each inside the other
        self.assertFalse(spatial.rings_intersect(SQUARE, FAR))
        cross_a = [[0, 1], [4, 1], [4, 1.5], [0, 1.5], [0, 1]]           # a thin bar through the square
        cross_b = [[1, -2], [1.5, -2], [1.5, 5], [1, 5], [1, -2]]        # a thin bar crossing it, no vertex inside
        self.assertTrue(spatial.rings_intersect(cross_a, cross_b))

    def test_touching_rings_count_as_intersecting(self):
        square1 = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        square2 = [[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]
        self.assertTrue(spatial.rings_intersect(square1, square2))


class TestRingIndex(unittest.TestCase):
    def test_covering_and_intersecting_use_cells(self):
        index = spatial.RingIndex([(SQUARE, "square"), (FAR, "far"), (INSIDE, "inside")], cell=1.0)
        self.assertEqual(index.count, 3)
        self.assertEqual(index.covering(1, 1), ["square", "inside"])
        self.assertEqual(index.covering(10.5, 10.5), ["far"])
        self.assertEqual(index.covering(5, 5), [])
        self.assertEqual(index.intersecting((1.9, 1.9, 2.5, 2.5)), ["square"])
        self.assertEqual(index.intersecting((-5, -5, 20, 20)), ["square", "far", "inside"])

    def test_negative_coordinates_bucket_correctly(self):
        ring = [[-100.5, 40.5], [-99.5, 40.5], [-99.5, 41.5], [-100.5, 41.5], [-100.5, 40.5]]
        index = spatial.RingIndex([(ring, "r")])
        self.assertEqual(index.covering(-100.0, 41.0), ["r"])
        self.assertEqual(index.covering(-100.9, 41.0), [])
