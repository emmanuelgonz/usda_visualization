import math
import random
import unittest

from viz import river_names


def reach(next_id, order, dis, up, *parts):
    return {"next": next_id, "ord": order, "dis": dis, "up": up, "parts": [list(p) for p in parts]}


def along(lon0, lon1, lat, n=5):
    return [[lon0 + (lon1 - lon0) * k / (n - 1), lat] for k in range(n)]


def chain_part(k, lat_mid=None):
    """Chain reach k (0 = A) runs east from lon -100 + 0.1k, five vertices, optionally bowed north."""
    part = along(-100.0 + 0.1 * k, -99.9 + 0.1 * k, 40.0)
    if lat_mid is not None:
        for vertex in part[1:4]:
            vertex[1] = lat_mid
    return part


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.references = [("Mainstem", [[-100.0, 40.0], [-99.0, 40.0]])]
        cls.reaches = {
            1: reach(2, 7, 100.0, 1000, chain_part(0)),                 # A
            2: reach(3, 7, 110.0, 1100, chain_part(1)),                 # B
            3: reach(4, 7, 120.0, 1200, chain_part(2, 40.022)),         # C, bowed 2.4 km north
            4: reach(5, 7, 130.0, 1300, chain_part(3)),                 # D
            5: reach(6, 7, 140.0, 1400, chain_part(4)),                 # E
            6: reach(None, 8, 300.0, 2600, along(-99.5, -99.4, 40.02)),  # F, 2.2 km off
            7: reach(4, 5, 10.0, 100, [[-99.7, 40.05], [-99.7, 40.025], [-99.7, 40.0]]),  # T
            8: reach(6, 7, 150.0, 1500, along(-99.6, -99.5, 39.9)),     # G, from the south
            9: reach(None, 6, 5.0, 50, along(-99.9, -99.8, 41.0)),      # Z, far
            10: reach(None, 7, 200.0, 2000, along(-99.3, -99.2, 40.0)),  # core order-7 mainstem for M
            11: reach(10, 4, 3.0, 30, along(-99.29, -99.21, 40.001)),   # M, order 4 mouth, 0.11 km off
            12: reach(None, 7, 210.0, 2100, along(-99.1, -99.0, 40.0)),  # core order-7 mainstem for K
            13: reach(12, 6, 30.0, 300, along(-99.09, -99.01, 40.001)),  # K, order 6 control, gap of 1
        }
        cls.index = river_names.ReferenceIndex(cls.references)
        cls.names = river_names.assign_names(cls.reaches, cls.references)


class TestAssignNames(Fixture):
    def test_core_rule_names_a_b_d_e(self):
        for reach_id in (1, 2, 4, 5):
            self.assertEqual(self.names.get(reach_id), "Mainstem")

    def test_bowed_reach_is_named_by_propagation_only(self):
        median, name = river_names.reach_match(self.index, self.reaches[3]["parts"])
        self.assertGreater(median, river_names.CORE_KM)
        self.assertLessEqual(median, river_names.LOOSE_KM)
        self.assertEqual(name, "Mainstem")
        self.assertEqual(self.names.get(3), "Mainstem")

    def test_tributary_is_unnamed(self):
        self.assertNotIn(7, self.names)

    def test_tributary_mouth_two_orders_below_its_named_downstream_reach_is_refused(self):
        for reach_id in (11, 13):
            median, name = river_names.reach_match(self.index, self.reaches[reach_id]["parts"])
            self.assertLess(median, river_names.CORE_KM)
            self.assertEqual(name, "Mainstem")
        self.assertEqual(self.names.get(10), "Mainstem")
        self.assertEqual(self.names.get(12), "Mainstem")
        self.assertNotIn(11, self.names)
        self.assertEqual(self.names.get(13), "Mainstem")

    def test_downstream_reach_of_higher_order_is_named_when_upstream_orders_equal(self):
        self.assertEqual(self.names.get(6), "Mainstem")

    def test_far_branch_g_is_unnamed(self):
        self.assertNotIn(8, self.names)

    def test_far_reach_z_is_unnamed_and_has_no_match(self):
        self.assertNotIn(9, self.names)
        median, name = river_names.reach_match(self.index, self.reaches[9]["parts"])
        self.assertEqual((median, name), (math.inf, None))

    def test_upstream_propagation_and_confluence_of_equals(self):
        reaches = {
            1: reach(3, 5, 10.0, 100, along(-100.0, -99.95, 40.02)),   # loose, order 5
            2: reach(3, 5, 20.0, 100, along(-100.0, -99.95, 40.021)),  # loose, order 5, larger dis
            3: reach(None, 6, 30.0, 200, along(-99.95, -99.9, 40.0)),  # core, order 6
        }
        names = river_names.assign_names(reaches, self.references)
        self.assertEqual(names, {3: "Mainstem", 2: "Mainstem"})

    def test_upstream_lower_order_without_confluence_is_not_named(self):
        reaches = {
            1: reach(2, 5, 10.0, 100, along(-100.0, -99.95, 40.02)),
            2: reach(None, 7, 30.0, 200, along(-99.95, -99.9, 40.0)),
        }
        names = river_names.assign_names(reaches, self.references)
        self.assertEqual(names, {2: "Mainstem"})

    def test_single_lower_order_upstream_without_a_confluence_is_not_named(self):
        reaches = {
            1: reach(2, 6, 10.0, 100, along(-100.0, -99.95, 40.02)),   # loose, order 6, alone
            2: reach(None, 7, 30.0, 200, along(-99.95, -99.9, 40.0)),  # core, order 7
        }
        names = river_names.assign_names(reaches, self.references)
        self.assertEqual(names, {2: "Mainstem"})

    def test_downstream_step_up_is_blocked_when_a_higher_order_reach_also_feeds_it(self):
        reaches = {
            1: reach(2, 6, 10.0, 100, along(-99.95, -99.9, 40.0)),     # core, order 6
            2: reach(None, 7, 30.0, 200, along(-99.9, -99.85, 40.02)),  # loose, order 7
            3: reach(2, 7, 25.0, 190, along(-99.9, -99.85, 41.0)),     # far order-7 branch
        }
        names = river_names.assign_names(reaches, self.references)
        self.assertEqual(names, {1: "Mainstem"})

    def test_deterministic(self):
        again = river_names.assign_names(self.reaches, self.references)
        self.assertEqual(list(again.items()), list(self.names.items()))
        reversed_reaches = dict(reversed(list(self.reaches.items())))
        reversed_refs = list(reversed(self.references))
        shuffled_items = list(self.reaches.items())
        random.Random(0).shuffle(shuffled_items)
        shuffled_refs = list(self.references)
        random.Random(0).shuffle(shuffled_refs)
        for reaches, refs in ((reversed_reaches, reversed_refs), (dict(shuffled_items), shuffled_refs),
                              (reversed_reaches, self.references)):
            self.assertEqual(river_names.assign_names(reaches, refs), self.names)

    def test_median_is_measured_to_the_named_reference_only(self):
        left = ("Left", [[-100.0, 40.0], [-99.0, 40.0]])
        right = ("Right", [[-100.0, 40.0135], [-99.0, 40.0135]])   # 1.5 km north of Left
        index = river_names.ReferenceIndex([left, right])
        on_left = [[-99.60 + 0.02 * k, 40.0018] for k in range(4)]    # 0.2 km from Left, 1.3 from Right
        on_right = [[-99.50 + 0.02 * k, 40.0117] for k in range(3)]   # 0.2 km from Right, 1.3 from Left
        # Equal halves: only half the vertices lie on the chosen name, so the median is inf.
        median, name = river_names.reach_match(index, [on_left[:3] + on_right])
        self.assertIn(name, ("Left", "Right"))
        self.assertEqual(median, math.inf)
        # One more vertex on Left: a majority lies on the named line, so the median is finite.
        median, name = river_names.reach_match(index, [on_left + on_right])
        self.assertEqual(name, "Left")
        self.assertLess(median, 0.5)
        # One more vertex on Right: Right is the mode and the Left vertices count as inf.
        median, name = river_names.reach_match(index, [on_left[:3] + on_right + [[-99.44, 40.0117]]])
        self.assertEqual(name, "Right")
        self.assertLess(median, 0.5)


class TestHelpers(unittest.TestCase):
    def test_km_scale(self):
        kx, ky = river_names.km_scale(60.0)
        self.assertAlmostEqual(kx, 55.5)
        self.assertEqual(ky, 111.0)

    def test_densify_spacing_and_endpoints(self):
        line = [[-100.0, 40.0], [-99.9, 40.0], [-99.9, 40.1]]
        dense = river_names.densify(line)
        self.assertEqual(dense[0], line[0])
        self.assertEqual(dense[-1], line[-1])
        self.assertIn(line[1], dense)
        for (x0, y0), (x1, y1) in zip(dense, dense[1:]):
            kx, ky = river_names.km_scale((y0 + y1) / 2)
            self.assertLessEqual(math.hypot((x1 - x0) * kx, (y1 - y0) * ky), river_names.STEP_KM + 1e-9)
        self.assertGreater(len(dense), 40)

    def test_nearest_within_and_beyond_the_block(self):
        index = river_names.ReferenceIndex([("Mainstem", [[-100.0, 40.0], [-99.0, 40.0]])])
        distance, name = index.nearest(-99.5, 40.009)
        self.assertEqual(name, "Mainstem")
        self.assertAlmostEqual(distance, 0.009 * 111.0, delta=0.05)
        self.assertEqual(index.nearest(-99.5, 40.2), (math.inf, None))


if __name__ == "__main__":
    unittest.main()
