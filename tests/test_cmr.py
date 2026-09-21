import contextlib
import io
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

        with contextlib.redirect_stdout(io.StringIO()):   # the paging progress lines
            entries = cmr.fetch_all(fake)
        self.assertEqual(len(entries), 3)
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


class TestPolygonRing(unittest.TestCase):
    def test_swaps_to_lon_lat_and_closes(self):
        entry = {"polygons": [["42.0 -94.0 42.0 -93.0 43.0 -93.0"]]}
        self.assertEqual(cmr.polygon_ring(entry),
                         [[-94.0, 42.0], [-93.0, 42.0], [-93.0, 43.0], [-94.0, 42.0]])

    def test_already_closed_ring_is_not_doubled(self):
        entry = {"polygons": [["42.0 -94.0 42.0 -93.0 43.0 -93.0 42.0 -94.0"]]}
        self.assertEqual(len(cmr.polygon_ring(entry)), 4)

    def test_missing_polygon_is_none(self):
        self.assertIsNone(cmr.polygon_ring({}))
        self.assertIsNone(cmr.polygon_ring({"polygons": []}))
        self.assertIsNone(cmr.polygon_ring({"polygons": [[]]}))


class TestFetchResponse(unittest.TestCase):
    def test_retries_then_returns_body_and_headers(self):
        import io
        from unittest import mock

        class Response(io.BytesIO):
            headers = {"CMR-Hits": "9675"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        calls = []

        def fake_urlopen(url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("boom")
            return Response(b"a,b\n1,2\n")

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("time.sleep"):
            body, headers = cmr.fetch_response("http://x")
        self.assertEqual(body, b"a,b\n1,2\n")
        self.assertEqual(headers.get("CMR-Hits"), "9675")
        self.assertEqual(len(calls), 2)

    def test_gives_up_after_three_failures(self):
        from unittest import mock

        def fake_urlopen(url, timeout):
            raise OSError("boom")

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("time.sleep"):
            with self.assertRaises(OSError):
                cmr.fetch_response("http://x")


if __name__ == "__main__":
    unittest.main()
