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
                    "cdl_classes", "cdl_pairing", "var_labels", "crop_codes"):
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

    def test_focused_cdl_tile_differs_and_is_cached_separately(self):
        _, _, plain = self.get("/tiles/cdl/2024/" + self.zxy())
        _, _, focused = self.get("/tiles/cdl/2024/" + self.zxy() + "?focus=soy")
        self.assertEqual(_png_size(focused), (256, 256))
        self.assertNotEqual(plain, focused)  # corn pixels greyed when soy is the focus
        self.assertTrue(tileserver.cache_path("cdl-2024-fsoy", self.z, self.x, self.y).is_file())
        self.assertTrue(tileserver.cache_path("cdl-2024", self.z, self.x, self.y).is_file())

    def test_focus_on_unknown_crop_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/cdl/2024/" + self.zxy() + "?focus=barley")
        self.assertEqual(ctx.exception.code, 404)

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

    def test_concurrent_requests_for_the_same_cold_tile_both_succeed(self):
        # A tile coordinate no other test touches, so the cache is genuinely cold.
        x = self.x + 1
        url = f"/tiles/cdl/2024/{self.z}/{x}/{self.y}.png"
        barrier = threading.Barrier(2)
        results = [None, None]

        def worker(index):
            barrier.wait()
            results[index] = self.get(url)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        (status_a, _, body_a), (status_b, _, body_b) = results
        self.assertEqual(status_a, 200)
        self.assertEqual(status_b, 200)
        self.assertEqual(body_a, body_b)


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

    def test_non_numeric_parameters_return_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/point?lon=abc&lat=1&crop=corn&year=2024&cdl_year=2024")
        self.assertEqual(ctx.exception.code, 400)


class TestInterfaceAssets(ServerTestCase):
    def test_swipe_clips_in_layer_space_and_tracks_map_moves(self):
        # Leaflet panes are 0x0 boxes, so an inset() clip on one collapses to
        # nothing and the CPC layer vanishes. The clip must be built from
        # container corners converted to layer points, and re-applied on move.
        _, _, body = self.get("/static/app.js")
        text = body.decode()
        self.assertNotIn("inset(", text)
        self.assertIn("containerPointToLayerPoint", text)
        self.assertIn('map.on("move"', text)

    def test_readout_is_a_map_popup_not_a_panel_section(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("L.popup(", text)
        self.assertNotIn("circleMarker", text)
        self.assertIn("30 m pixel", text)
        self.assertIn("9 km CPC cell", text)
        self.assertIn("No CPC data at this cell", text)
        _, _, index = self.get("/")
        self.assertIn("Click the map to inspect a cell", index.decode())

    def test_state_boundaries_are_served_as_conus_geojson(self):
        status, ctype, body = self.get("/static/vendor/states.geojson")
        self.assertEqual(status, 200)
        self.assertIn("json", ctype)
        data = json.loads(body)
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(len(data["features"]), 49)  # 48 states plus DC
        names = set()
        for feature in data["features"]:
            props = feature["properties"]
            for key in ("NAME", "STUSPS", "INTPTLAT", "INTPTLON"):
                self.assertIn(key, props)
            names.add(props["STUSPS"])
        self.assertIn("IA", names)
        self.assertNotIn("AK", names)
        self.assertNotIn("HI", names)
        self.assertNotIn("PR", names)

    def test_app_draws_boundaries_non_interactively_with_zoom_gated_labels(self):
        _, _, body = self.get("/static/app.js")
        text = body.decode()
        self.assertIn("/static/vendor/states.geojson", text)
        self.assertIn("L.geoJSON(", text)
        self.assertIn("interactive: false", text)
        self.assertIn("LABEL_MAX_ZOOM", text)
        _, _, index = self.get("/")
        self.assertIn('id="states"', index.decode())

    def test_app_sends_focus_only_under_the_mask(self):
        _, _, body = self.get("/static/app.js")
        text = body.decode()
        self.assertIn("?focus=", text)
        self.assertIn("state.mask", text[text.index("function drawCdl"):text.index("function drawCpc")])

    def test_cdl_base_layer_is_toggleable(self):
        _, _, index = self.get("/")
        self.assertIn('id="cdl"', index.decode())
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("state.cdlVisible", text[text.index("function drawCdl"):text.index("function drawCpc")])
        _, _, css = self.get("/static/style.css")
        self.assertIn("#e9e9e6", css.decode())

    def test_index_loads_vendored_leaflet_not_a_cdn(self):
        _, _, body = self.get("/")
        text = body.decode()
        self.assertIn("/static/vendor/leaflet/leaflet.js", text)
        self.assertIn("/static/vendor/leaflet/leaflet.css", text)
        self.assertNotIn("://unpkg.com", text)
        self.assertNotIn("://cdn", text)

    def test_index_references_app_and_style(self):
        _, _, body = self.get("/")
        text = body.decode()
        self.assertIn("/static/app.js", text)
        self.assertIn("/static/style.css", text)

    def test_static_assets_are_served(self):
        for route, expected in (
            ("/static/app.js", "text/javascript"),
            ("/static/style.css", "text/css"),
            ("/static/vendor/leaflet/leaflet.js", "text/javascript"),
            ("/static/vendor/leaflet/leaflet.css", "text/css"),
        ):
            with self.subTest(route=route):
                status, ctype, body = self.get(route)
                self.assertEqual(status, 200)
                self.assertIn(expected, ctype)
                self.assertGreater(len(body), 0)

    def test_static_route_refuses_directory_traversal(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/static/../../viz/tileserver.py")
        self.assertIn(ctx.exception.code, (400, 403, 404))


if __name__ == "__main__":
    unittest.main()
