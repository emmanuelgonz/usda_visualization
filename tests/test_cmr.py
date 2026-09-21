import json
import shutil
import tempfile
import unittest
from pathlib import Path

from viz import cmr


class TestPageUrl(unittest.TestCase):
    def test_emit_style_url(self):
        url = cmr.page_url("EMITL2ARFL", 3)
        self.assertTrue(url.startswith(cmr.CMR_URL + "?"))
        self.assertIn("short_name=EMITL2ARFL", url)
        self.assertIn("bounding_box=-125,24.4,-66.9,49.4", url)
        self.assertIn("page_size=2000", url)
        self.assertIn("page_num=3", url)
        self.assertNotIn("version=", url)
        self.assertNotIn("temporal=", url)

    def test_ecostress_style_url_with_version_and_open_temporal(self):
        url = cmr.page_url("ECO_L2_LSTE", 1, version="002", temporal="2022-01-01T00:00:00Z,")
        self.assertIn("version=002", url)
        self.assertIn("temporal=2022-01-01T00:00:00Z,", url)


class TestFetchAll(unittest.TestCase):
    def test_pages_until_an_empty_page(self):
        pages = {1: [{"a": 1}, {"a": 2}], 2: [{"a": 3}], 3: []}
        asked = []

        def fake(page_num):
            asked.append(page_num)
            return pages.get(page_num, [])

        self.assertEqual(len(cmr.fetch_all(fake)), 3)
        self.assertEqual(asked, [1, 2, 3])


class TestLink(unittest.TestCase):
    ENTRY = {"links": [
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/s3#", "href": "s3://bucket/x.nc"},
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://d/x.nc"},
        {"rel": "http://esipfed.org/ns/fedsearch/1.1/browse#", "href": "https://d/x.png"},
    ]}

    def test_picks_first_http_href_for_suffix(self):
        self.assertEqual(cmr.link(self.ENTRY, "/data#"), "https://d/x.nc")
        self.assertEqual(cmr.link(self.ENTRY, "/browse#"), "https://d/x.png")

    def test_missing_suffix_is_none(self):
        self.assertIsNone(cmr.link(self.ENTRY, "/metadata#"))

    def test_s3_only_is_none(self):
        entry = {"links": [{"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "s3://b/x"}]}
        self.assertIsNone(cmr.link(entry, "/data#"))


class TestWriteGeojson(unittest.TestCase):
    def test_atomic_feature_collection(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        out = tmp / "sub" / "f.geojson"
        cmr.write_geojson([{"type": "Feature", "geometry": None, "properties": {}}], out)
        self.assertEqual(json.loads(out.read_text())["type"], "FeatureCollection")
        self.assertFalse(list(tmp.glob("**/*.part")))


if __name__ == "__main__":
    unittest.main()
