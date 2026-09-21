import unittest

from viz import cmr, fetch_hls, hls

HEADER = "Granule UR,Producer Granule ID,Start Time,End Time,Online Access URLs,Browse URLs,Cloud Cover,Day/Night,Size\n"


def csv_rows(*specs):
    return HEADER + "".join(f"{ur},x,{start},{start},https://a/x.tif,,{cloud},DAY,1\n" for ur, start, cloud in specs)


class TestUrls(unittest.TestCase):
    def test_csv_url_is_bounded_to_the_month(self):
        url = fetch_hls.csv_url("HLSS30", "2025-07", 3)
        self.assertTrue(url.startswith("https://cmr.earthdata.nasa.gov/search/granules.csv?"))
        self.assertIn("short_name=HLSS30", url)
        self.assertIn("version=2.0", url)
        self.assertIn("bounding_box=" + cmr.BBOX, url)
        self.assertIn("temporal=2025-07-01T00:00:00Z,2025-08-01T00:00:00Z", url)
        self.assertIn("page_size=2000", url)
        self.assertIn("page_num=3", url)

    def test_tile_urls_try_s30_then_l30(self):
        urls = fetch_hls.tile_urls("T15TVH")
        self.assertEqual(len(urls), 2)
        self.assertIn("short_name=HLSS30", urls[0])
        self.assertIn("granule_ur=HLS.S30.T15TVH.*", urls[0])
        self.assertIn("options[granule_ur][pattern]=true", urls[0])
        self.assertIn("page_size=1", urls[0])
        self.assertIn("short_name=HLSL30", urls[1])
        self.assertIn("granule_ur=HLS.L30.T15TVH.*", urls[1])
        self.assertTrue(urls[0].startswith(cmr.CMR_URL + "?"))


class patch_page_size:
    """Temporarily shrink cmr.PAGE_SIZE so a two-page test needs four rows, not 4,000."""

    def __init__(self, size):
        self.size = size

    def __enter__(self):
        self.saved = cmr.PAGE_SIZE
        cmr.PAGE_SIZE = self.size

    def __exit__(self, *args):
        cmr.PAGE_SIZE = self.saved
        return False


class TestFetchMonth(unittest.TestCase):
    def test_pages_by_hits_and_filters_to_the_month(self):
        pages = {
            1: csv_rows(("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10),
                        ("HLS.S30.T15TVH.2025200T170000.v2.0", "2025-07-19T17:00:00Z", 20)),
            2: csv_rows(("HLS.S30.T15TVH.2025201T170000.v2.0", "2025-07-20T17:00:00Z", 30),
                        ("HLS.S30.T15TVH.2025213T000000.v2.0", "2025-08-01T00:00:00Z", 0)),
        }
        asked = []

        def fake(url):
            page = int(url.rsplit("page_num=", 1)[1])
            asked.append(page)
            return pages[page].encode(), {"CMR-Hits": "4"}

        with patch_page_size(2):
            rows = fetch_hls.fetch_month("HLSS30", "2025-07", fetch_fn=fake)
        self.assertEqual(asked, [1, 2])
        self.assertEqual([r["date"] for r in rows], ["2025-07-18", "2025-07-19", "2025-07-20"])

    def test_zero_hits_is_one_request_and_no_rows(self):
        asked = []

        def fake(url):
            asked.append(url)
            return HEADER.encode(), {"CMR-Hits": "0"}

        self.assertEqual(fetch_hls.fetch_month("HLSL30", "2022-01", fetch_fn=fake), [])
        self.assertEqual(len(asked), 1)


class TestFetchRing(unittest.TestCase):
    def test_takes_the_first_polygon_from_the_first_collection_that_has_one(self):
        asked = []

        def fake(url):
            asked.append(url)
            if "HLSS30" in url:
                return []
            return [{"polygons": [["42.0 -94.0 42.0 -93.0 43.0 -93.0"]]}]

        ring = fetch_hls.fetch_ring("T15TVH", fetch_page_fn=fake)
        self.assertEqual(ring, [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 42.0]])
        self.assertEqual(len(asked), 2)

    def test_none_when_neither_collection_has_a_polygon(self):
        self.assertIsNone(fetch_hls.fetch_ring("T15TVH", fetch_page_fn=lambda url: [{"polygons": []}]))


class TestMain(unittest.TestCase):
    def test_skips_frozen_months_and_fills_missing_rings(self):
        import shutil
        import tempfile
        from pathlib import Path
        from unittest import mock

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db = tmp / "hls.sqlite"
        seed = hls.Store(db)
        seed.replace_month("S30", "2025-05", [], "2025-12-01T00:00:00+00:00")   # frozen
        seed.replace_month("L30", "2025-05", [], "2025-12-01T00:00:00+00:00")   # frozen
        seed.close()

        fetched = []

        # The one granule row belongs to S30, so only the HLSS30 call returns it; the
        # per-sensor month counts then sum to the one distinct acquisition.
        def fake_month(short_name, month, fetch_fn=None):
            fetched.append((short_name, month))
            if month == "2025-06" and short_name == "HLSS30":
                return hls.parse_csv(csv_rows(("HLS.S30.T15TVH.2025160T170000.v2.0", "2025-06-09T17:00:00Z", 10)))
            return []

        rings = []

        def fake_ring(tile, fetch_page_fn=None):
            rings.append(tile)
            return [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 42.0]]

        with mock.patch.object(fetch_hls, "fetch_month", fake_month), \
             mock.patch.object(fetch_hls, "fetch_ring", fake_ring), \
             mock.patch.object(fetch_hls, "current_month", lambda: "2025-06"), \
             mock.patch.object(fetch_hls.paths, "HLS_DB", db):
            self.assertEqual(fetch_hls.main(["--from", "2025-05"]), 0)

        self.assertEqual(sorted(fetched), [("HLSL30", "2025-06"), ("HLSS30", "2025-06")])
        self.assertEqual(rings, ["T15TVH"])
        store = hls.Store(db, read_only=True)
        self.addCleanup(store.close)
        self.assertEqual(store.summary()["count"], 1)
        self.assertEqual(store.summary()["tiles"], 1)
        self.assertEqual(store.missing_tiles(), [])


if __name__ == "__main__":
    unittest.main()
