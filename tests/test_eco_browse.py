import unittest

from viz import eco_browse

SWATH = "ECOv002_L2_LSTE_39607_006_20250701T061657_0713_01"
TILED = "ECOv002_L2T_LSTE_39607_006_15TUF_20250701T061657_0713_01"
BASE = "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-public/ECO_L2T_LSTE.002/" + TILED + "/" + TILED
BROWSE = "http://esipfed.org/ns/fedsearch/1.1/browse#"
S3 = "http://esipfed.org/ns/fedsearch/1.1/s3#"


def entry_with_browse(tiled_id=TILED):
    suffixes = ["_LST.jpeg", "_QC.jpeg", "_cloud.jpeg", "_water.jpeg", "_EmisWB.jpeg",
                "_LST_err.jpeg", "_height.jpeg", "_view_zenith.jpeg", ".png"]
    links = [{"rel": BROWSE, "href": BASE + s} for s in suffixes]
    links += [{"rel": BROWSE, "href": "s3://lp-prod-public/x" + s} for s in suffixes]
    links.append({"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": BASE + "_LST.tif"})
    return {"id": "G1", "title": tiled_id, "producer_granule_id": tiled_id,
            "time_start": "2025-07-01T06:16:57.000Z", "day_night_flag": "Night", "links": links}


class TestParsing(unittest.TestCase):
    def test_parse_swath_id(self):
        self.assertEqual(eco_browse.parse_swath_id(SWATH), ("39607", "006"))

    def test_parse_rejects_tiled_and_garbage(self):
        for bad in (TILED, "garbage", ""):
            with self.assertRaises(ValueError):
                eco_browse.parse_swath_id(bad)

    def test_query_url(self):
        url = eco_browse.query_url("39607", "006", -93.5, 42.25)
        self.assertTrue(url.startswith("https://cmr.earthdata.nasa.gov/search/granules.json?"))
        self.assertIn("short_name=ECO_L2T_LSTE", url)
        self.assertIn("version=002", url)
        self.assertIn("readable_granule_name%5B%5D=ECOv002_L2T_LSTE_39607_006_%2A", url)
        self.assertIn("options%5Breadable_granule_name%5D%5Bpattern%5D=true", url)
        self.assertIn("point=-93.500000%2C42.250000", url)

    def test_tile_from_id(self):
        self.assertEqual(eco_browse.tile_from_id(TILED), "15TUF")

    def test_browse_links_pick_three_by_suffix(self):
        links = eco_browse.browse_links(entry_with_browse())
        self.assertEqual(links, {"lst": BASE + "_LST.jpeg", "qc": BASE + "_QC.jpeg",
                                 "cloud": BASE + "_cloud.jpeg"})

    def test_missing_kind_is_absent(self):
        entry = {"links": [{"rel": BROWSE, "href": BASE + "_QC.jpeg"}]}
        self.assertEqual(eco_browse.browse_links(entry), {"qc": BASE + "_QC.jpeg"})


class TestLookup(unittest.TestCase):
    def setUp(self):
        eco_browse.clear_cache()

    def test_first_entry_with_an_lst_link_wins(self):
        bare = {"title": "x", "links": []}
        calls = []

        def fetch(url):
            calls.append(url)
            return [bare, entry_with_browse()]

        result = eco_browse.lookup(SWATH, -93.5, 42.25, fetch=fetch)
        self.assertEqual(result["id"], TILED)
        self.assertEqual(result["tile"], "15TUF")
        self.assertEqual(result["start"], "2025-07-01T06:16:57.000Z")
        self.assertEqual(result["daynight"], "Night")
        self.assertEqual(result["browse"]["lst"], BASE + "_LST.jpeg")
        self.assertIn("point=", calls[0])

    def test_empty_page_is_none_and_cached(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return []

        self.assertIsNone(eco_browse.lookup(SWATH, -93.5, 42.25, fetch=fetch))
        self.assertIsNone(eco_browse.lookup(SWATH, -93.5, 42.25, fetch=fetch))
        self.assertEqual(len(calls), 1)

    def test_second_call_hits_the_cache(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return [entry_with_browse()]

        first = eco_browse.lookup(SWATH, -93.5, 42.25, fetch=fetch)
        second = eco_browse.lookup(SWATH, -93.5004, 42.2504, fetch=fetch)   # same rounded key
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_bad_id_raises_value_error(self):
        with self.assertRaises(ValueError):
            eco_browse.lookup("nope", 0, 0, fetch=lambda url: [])

    def test_short_tiled_id_raises_index_error(self):
        with self.assertRaises(IndexError):
            eco_browse.lookup(SWATH, -93.5, 42.25,
                              fetch=lambda url: [entry_with_browse("ECOv002_L2T_LSTE_short")])

    def test_cache_drops_the_oldest_past_the_cap(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return []

        for i in range(eco_browse.CACHE_CAP + 1):
            eco_browse.lookup(SWATH, -100 + i * 0.01, 40.0, fetch=fetch)
        self.assertEqual(len(calls), eco_browse.CACHE_CAP + 1)
        eco_browse.lookup(SWATH, -100.0, 40.0, fetch=fetch)       # first key was evicted
        self.assertEqual(len(calls), eco_browse.CACHE_CAP + 2)
        eco_browse.lookup(SWATH, -100 + 0.01 * eco_browse.CACHE_CAP, 40.0, fetch=fetch)  # newest kept
        self.assertEqual(len(calls), eco_browse.CACHE_CAP + 2)


if __name__ == "__main__":
    unittest.main()
