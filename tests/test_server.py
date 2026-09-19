import json
import shutil
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests import fixtures
from viz import paths, tileserver


def _png_size(blob):
    return struct.unpack(">II", blob[16:24])


class ServerTestCase(unittest.TestCase):
    """Boots a real server against fixture rasters on a throwaway data root."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls._saved = (paths.DATA, paths.CPC_DATA, paths.CDL_DATA, paths.MASK_DATA,
                      paths.CATALOG, paths.TILE_CACHE)

        paths.DATA = cls.tmp / "data"
        paths.CPC_DATA = paths.DATA / "cpc"
        paths.CDL_DATA = paths.DATA / "cdl"
        paths.MASK_DATA = paths.DATA / "masks"
        paths.CATALOG = paths.DATA / "catalog.json"
        paths.TILE_CACHE = cls.tmp / "cache" / "tiles"

        (paths.CPC_DATA / "corn" / "cond").mkdir(parents=True)
        paths.CDL_DATA.mkdir(parents=True)
        paths.MASK_DATA.mkdir(parents=True)

        fixtures.write_cpc_like(paths.CPC_DATA / "corn" / "cond" / "cornCond24w30.tif", fill=3.5)
        fixtures.write_cdl_like(paths.CDL_DATA / "2024_30m_cdls.tif")

        from viz import prepare
        prepare.build_mask(str(paths.CDL_DATA / "2024_30m_cdls.tif"), "corn",
                           paths.MASK_DATA / "2024_corn_frac9km.tif")
        paths.CATALOG.write_text(json.dumps(
            prepare.build_catalog(paths.CPC_DATA, paths.CDL_DATA, paths.MASK_DATA)))

        # Tiles are computed from the fixtures' own geotransform. The fixtures sit
        # at the canonical CPC grid origin (127.35 W 48.20 N), not the Corn Belt,
        # so a hardcoded tile would miss them and every assertion would pass on
        # empty space.
        cls.z, cls.x, cls.y = fixtures.tile_covering(paths.CDL_DATA / "2024_30m_cdls.tif")
        cls.lon, cls.lat = fixtures.fixture_lonlat(paths.CDL_DATA / "2024_30m_cdls.tif")

        cls.server = tileserver.make_server(0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def zxy(cls):
        return f"{cls.z}/{cls.x}/{cls.y}.png"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        (paths.DATA, paths.CPC_DATA, paths.CDL_DATA, paths.MASK_DATA,
         paths.CATALOG, paths.TILE_CACHE) = cls._saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=30) as r:
            return r.status, r.headers.get("Content-Type"), r.read()


class TestStaticRoutes(ServerTestCase):
    def test_root_serves_html(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"<html", body.lower())

    def test_catalog_is_json_with_expected_keys(self):
        status, ctype, body = self.get("/api/catalog")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        catalog = json.loads(body)
        for key in ("crops", "vars", "cpc", "cdl_years", "mask_years",
                    "cdl_classes", "cdl_pairing"):
            self.assertIn(key, catalog)

    def test_catalog_reports_the_fixture_mask_year(self):
        _, _, body = self.get("/api/catalog")
        self.assertEqual(json.loads(body)["mask_years"], [])  # only corn built, not all four

    def test_unknown_route_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/no/such/route")
        self.assertEqual(ctx.exception.code, 404)


class TestTileRoutes(ServerTestCase):
    def test_cdl_tile_is_a_256_square_png(self):
        status, ctype, body = self.get("/tiles/cdl/2024/" + self.zxy())
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "image/png")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(_png_size(body), (256, 256))

    def test_cpc_tile_is_a_256_square_png(self):
        status, _, body = self.get("/tiles/cpc/corn/cond/2024/30/" + self.zxy())
        self.assertEqual(status, 200)
        self.assertEqual(_png_size(body), (256, 256))

    def test_masked_cpc_tile_renders(self):
        status, _, body = self.get("/tiles/cpc/corn/cond/2024/30/" + self.zxy() + "?mask=2024")
        self.assertEqual(status, 200)
        self.assertEqual(_png_size(body), (256, 256))

    def test_masked_and_unmasked_tiles_differ(self):
        # Both tiles cover real fixture data, so a difference here means the
        # mask actually modulated alpha rather than both being empty.
        _, _, plain = self.get("/tiles/cpc/corn/cond/2024/30/" + self.zxy())
        _, _, masked = self.get("/tiles/cpc/corn/cond/2024/30/" + self.zxy() + "?mask=2024")
        self.assertNotEqual(plain, masked)

    def test_second_request_is_served_from_the_disk_cache(self):
        self.get("/tiles/cdl/2024/" + self.zxy())
        cached = tileserver.cache_path("cdl-2024", self.z, self.x, self.y)
        self.assertTrue(cached.is_file())

    def test_missing_cpc_week_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/cpc/corn/cond/2024/99/" + self.zxy())
        self.assertEqual(ctx.exception.code, 404)

    def test_missing_cdl_year_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/cdl/1999/" + self.zxy())
        self.assertEqual(ctx.exception.code, 404)

    def test_malformed_tile_coordinates_return_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/cdl/2024/a/b/c.png")
        self.assertIn(ctx.exception.code, (400, 404))


class TestPointRoute(ServerTestCase):
    def point_url(self):
        return (f"/api/point?lon={self.lon:.6f}&lat={self.lat:.6f}"
                "&crop=corn&year=2024&cdl_year=2024")

    def test_reports_cdl_class_and_crop_cover(self):
        status, ctype, body = self.get(self.point_url())
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        report = json.loads(body)
        for key in ("lon", "lat", "cdl_class", "cover", "series"):
            self.assertIn(key, report)
        self.assertIn("primary", report["cover"])
        self.assertIn("double", report["cover"])

    def test_resolves_a_real_class_name_inside_the_fixture(self):
        # The point is the fixture's own centre, so this must hit real data.
        report = json.loads(self.get(self.point_url())[2])
        self.assertIn(report["cdl_class"], ("Background", "Corn"))
        self.assertIsNotNone(report["cdl_code"])

    def test_series_carries_both_variables_keyed_by_week(self):
        _, _, body = self.get(self.point_url())
        series = json.loads(body)["series"]
        self.assertIn("cond", series)
        self.assertIn("prog", series)

    def test_series_returns_the_week_present_in_the_fixture(self):
        series = json.loads(self.get(self.point_url())[2])["series"]
        self.assertEqual([p["week"] for p in series["cond"]], [30])

    def test_missing_parameters_return_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/point?lon=-93.62")
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
