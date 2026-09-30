import unittest

from viz import archetypes, cmr, registry
from viz.archetypes import tiled

HEADER = "Granule UR,Producer Granule ID,Start Time,End Time,Online Access URLs,Browse URLs,Cloud Cover,Day/Night,Size\n"
BBOX = (-125.0, 24.4, -66.9, 49.4)

MISSION = registry.parse({
    "region": {"name": "T", "bbox": list(BBOX)},
    "missions": [{
        "key": "hls", "name": "HLS", "label": "HLS", "archetype": "tiled", "grid": "mgrs",
        "cmr": [{"short_name": "HLSL30", "version": "2.0", "implies": {"sensor": "L30"}},
                {"short_name": "HLSS30", "version": "2.0", "implies": {"sensor": "S30"}}],
        "since": "2022-01",
        "tile_from": {"field": "title", "pattern": "^HLS\\.[LS]30\\.(T[0-9]{2}[A-Z]{3})\\."},
        "attributes": {"cloud": {"from": "cloud_cover", "type": "number"},
                       "sensor": {"from": "sensor", "type": "text"},
                       "daynight": {"from": "day_night_flag", "type": "text"}},
        "filters": [], "browse": {"source": "links", "match": "\\.jpg$"}, "links": {}, "style": {}}]
}).mission("hls")
S30 = MISSION.cmr[1]


def row(ur, start, cloud, browse=""):
    return f'{ur},x,{start},{start},https://a/x.tif,{browse},{cloud},DAY,1\n'


class patch_page_size:
    def __init__(self, size):
        self.size = size

    def __enter__(self):
        self.saved = cmr.PAGE_SIZE
        cmr.PAGE_SIZE = self.size

    def __exit__(self, *args):
        cmr.PAGE_SIZE = self.saved
        return False


class TestUrlAndParse(unittest.TestCase):
    def test_csv_url_is_bounded_to_the_month_and_region(self):
        url = tiled.csv_url(S30, BBOX, "2025-07", 3)
        self.assertTrue(url.startswith(tiled.CSV_URL + "?"))
        for part in ("short_name=HLSS30", "version=2.0", "bounding_box=-125.0,24.4,-66.9,49.4",
                     "temporal=2025-07-01T00:00:00Z,2025-08-01T00:00:00Z", "page_size=2000", "page_num=3"):
            self.assertIn(part, url)

    def test_parse_rows_with_tile_attributes_and_implied_sensor(self):
        text = HEADER + row("HLS.S30.T15TVH.2025203T170849.v2.0", "2025-07-22T17:08:49.000Z", "14", "https://a/x.jpg") \
                      + row("HLS.S30.T15TVH.2025204T170849.v2.0", "2025-07-23T17:08:49.000Z", "", "") \
                      + row("ECOv002_L2_LSTE_1_1_20250722T000000_0713_01", "2025-07-22T00:00:00Z", "1", "")
        rows = tiled.parse_csv(MISSION, S30, text)
        self.assertEqual([r["id"] for r in rows], ["HLS.S30.T15TVH.2025203T170849.v2.0", "HLS.S30.T15TVH.2025204T170849.v2.0"])
        first = rows[0]
        self.assertEqual((first["tile"], first["cloud"], first["sensor"], first["daynight"], first["browse"]),
                         ("T15TVH", 14.0, "S30", "DAY", "https://a/x.jpg"))
        self.assertEqual(first["start"], "2025-07-22T17:08:49.000Z")
        self.assertIsNone(rows[1]["cloud"])
        self.assertIsNone(rows[1]["browse"])

    def test_browse_from_quoted_multi_url_cell(self):
        text = HEADER + row("HLS.S30.T15TVH.2025203T170849.v2.0", "2025-07-22T17:08:49Z", "1",
                            '"https://a/x.tif,https://a/thumb.jpg,https://a/y.png"')
        self.assertEqual(tiled.parse_csv(MISSION, S30, text)[0]["browse"], "https://a/thumb.jpg")


class TestFetchMonth(unittest.TestCase):
    def test_pages_by_hits_and_filters_to_the_month(self):
        pages = {
            1: HEADER + row("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10)
                      + row("HLS.S30.T15TVH.2025200T170000.v2.0", "2025-07-19T17:00:00Z", 20),
            2: HEADER + row("HLS.S30.T15TVH.2025201T170000.v2.0", "2025-07-20T17:00:00Z", 30)
                      + row("HLS.S30.T15TVH.2025213T000000.v2.0", "2025-08-01T00:00:00Z", 0),
        }
        asked = []

        def fake(url):
            page = int(url.rsplit("page_num=", 1)[1])
            asked.append(page)
            return pages[page].encode(), {"CMR-Hits": "4"}

        with patch_page_size(2):
            rows = tiled.fetch_month(MISSION, S30, BBOX, "2025-07", fetch_fn=fake)
        self.assertEqual(asked, [1, 2])
        self.assertEqual([r["start"][:10] for r in rows], ["2025-07-18", "2025-07-19", "2025-07-20"])

    def test_missing_hits_header_and_short_delivery_raise(self):
        with self.assertRaisesRegex(ValueError, "CMR-Hits"):
            tiled.fetch_month(MISSION, S30, BBOX, "2025-07", fetch_fn=lambda url: (HEADER.encode(), {}))
        short = HEADER + row("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10)
        with self.assertRaisesRegex(ValueError, "delivered 1"):
            tiled.fetch_month(MISSION, S30, BBOX, "2025-07", fetch_fn=lambda url: (short.encode(), {"CMR-Hits": "2"}))

    def test_zero_hits_is_one_request(self):
        asked = []

        def fake(url):
            asked.append(url)
            return HEADER.encode(), {"CMR-Hits": "0"}

        self.assertEqual(tiled.fetch_month(MISSION, S30, BBOX, "2022-01", fetch_fn=fake), [])
        self.assertEqual(len(asked), 1)


class TestPackage(unittest.TestCase):
    def test_get_returns_the_module(self):
        self.assertIs(archetypes.get("tiled"), tiled)
        self.assertEqual(archetypes.NAMES, ("swath", "tiled"))
