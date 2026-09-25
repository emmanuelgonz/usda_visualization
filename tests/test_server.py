import json
import re
import shutil
import struct
import subprocess
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
                      paths.CATALOG, paths.TILE_CACHE, paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS,
                      paths.HLS_DB)

        paths.DATA = cls.tmp / "data"
        paths.CPC_DATA = paths.DATA / "cpc"
        paths.CDL_DATA = paths.DATA / "cdl"
        paths.MASK_DATA = paths.DATA / "masks"
        paths.CATALOG = paths.DATA / "catalog.json"
        paths.TILE_CACHE = cls.tmp / "cache" / "tiles"
        paths.EMIT_FOOTPRINTS = paths.DATA / "emit" / "footprints.geojson"
        paths.ECO_FOOTPRINTS = paths.DATA / "eco" / "footprints.geojson"
        paths.HLS_DB = paths.DATA / "hls" / "hls.sqlite"

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

        def square(lon, lat, half):
            return [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
                    [lon - half, lat + half], [lon - half, lat - half]]

        def feature(fid, ring, start, cloud):
            return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"id": fid, "start": start, "end": start, "cloud": cloud,
                                   "year": int(start[:4]), "browse": "https://b/x.png", "data": None}}

        paths.EMIT_FOOTPRINTS.parent.mkdir(parents=True)
        paths.EMIT_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": [
            feature("near-old", square(cls.lon, cls.lat, 0.5), "2023-07-01T18:00:00Z", 12.0),
            feature("near-new", square(cls.lon, cls.lat, 0.5), "2025-07-20T18:00:00Z", 40.0),
            feature("far", square(cls.lon + 20, cls.lat, 0.5), "2024-07-01T18:00:00Z", 1.0),
            feature("no-tile", square(cls.lon + 10, cls.lat, 0.5), "2025-07-20T18:00:00Z", 5.0),
        ]}))

        def eco_feature(fid, ring, start, daynight):
            return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"id": fid, "start": start, "end": start, "daynight": daynight,
                                   "year": int(start[:4]), "orbit": 1}}

        paths.ECO_FOOTPRINTS.parent.mkdir(parents=True)
        paths.ECO_FOOTPRINTS.write_text(json.dumps({"type": "FeatureCollection", "features": [
            eco_feature("eco-day", square(cls.lon, cls.lat, 3.0), "2025-07-20T17:59:30Z", "DAY"),
            eco_feature("eco-night", square(cls.lon, cls.lat, 3.0), "2025-07-21T05:00:00Z", "NIGHT"),
            eco_feature("eco-far", square(cls.lon + 20, cls.lat, 3.0), "2025-07-20T18:00:00Z", "DAY"),
        ]}))
        # Attach coincidence pairs to the EMIT fixture the way ./run.sh footprints would.
        from viz import coincidence
        emit_doc = json.loads(paths.EMIT_FOOTPRINTS.read_text())
        eco_doc = json.loads(paths.ECO_FOOTPRINTS.read_text())
        coincidence.pair(emit_doc["features"], eco_doc["features"])
        paths.EMIT_FOOTPRINTS.write_text(json.dumps(emit_doc))

        # An HLS store with one tile over the fixture centre and one far away.
        from viz import hls

        def acq(ur, start, cloud):
            sensor, tile = hls.parse_ur(ur)
            return {"id": ur, "tile": tile, "date": start[:10], "time": start, "sensor": sensor, "cloud": cloud}

        hls_store = hls.Store(paths.HLS_DB)
        hls_store.replace_month("S30", "2025-07", [
            acq("HLS.S30.T99ZZZ.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10),
            acq("HLS.S30.T99ZZZ.2025204T170000.v2.0", "2025-07-23T17:00:00Z", 10),
            acq("HLS.S30.T98ZZZ.2025201T170000.v2.0", "2025-07-20T17:00:00Z", 0),
        ], "2025-08-02T00:00:00+00:00")
        hls_store.replace_month("L30", "2025-07", [
            acq("HLS.L30.T99ZZZ.2025202T160000.v2.0", "2025-07-21T16:00:00Z", 80),
        ], "2025-08-02T00:00:00+00:00")
        hls_store.put_tile("T99ZZZ", square(cls.lon, cls.lat, 0.5))
        hls_store.put_tile("T98ZZZ", square(cls.lon + 20, cls.lat, 0.5))
        # Attach the HLS lists to the EMIT fixture the way ./run.sh hls would.
        from viz import hls_pairs
        emit_doc = json.loads(paths.EMIT_FOOTPRINTS.read_text())
        hls_pairs.pair(emit_doc["features"], hls_store, hls.TileIndex(hls_store))
        paths.EMIT_FOOTPRINTS.write_text(json.dumps(emit_doc))
        hls_store.close()

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
         paths.CATALOG, paths.TILE_CACHE, paths.EMIT_FOOTPRINTS, paths.ECO_FOOTPRINTS,
         paths.HLS_DB) = cls._saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=30) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def point_url(self):
        return (f"/api/point?lon={self.lon:.6f}&lat={self.lat:.6f}"
                "&crop=corn&year=2024&cdl_year=2024")


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

    def test_catalog_carries_a_server_token_for_tile_cache_busting(self):
        _, _, body = self.get("/api/catalog")
        token = json.loads(body)["server_token"]
        self.assertTrue(token)
        self.assertEqual(token, tileserver.SERVER_TOKEN)

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
        self.assertTrue(tileserver.cache_path("cdl-2024-fsoy-white", self.z, self.x, self.y).is_file())
        self.assertTrue(tileserver.cache_path("cdl-2024", self.z, self.x, self.y).is_file())

    def test_grey_and_white_focus_render_and_cache_separately(self):
        _, _, white = self.get("/tiles/cdl/2024/" + self.zxy() + "?focus=soy&dim=white")
        _, _, grey = self.get("/tiles/cdl/2024/" + self.zxy() + "?focus=soy&dim=grey")
        self.assertNotEqual(white, grey)
        self.assertTrue(tileserver.cache_path("cdl-2024-fsoy-white", self.z, self.x, self.y).is_file())
        self.assertTrue(tileserver.cache_path("cdl-2024-fsoy-grey", self.z, self.x, self.y).is_file())

    def test_unknown_dim_style_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/cdl/2024/" + self.zxy() + "?focus=soy&dim=sepia")
        self.assertEqual(ctx.exception.code, 404)

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


class TestEmitRoutes(ServerTestCase):
    def test_footprints_are_served_as_geojson(self):
        status, ctype, body = self.get("/api/emit/footprints.geojson")
        self.assertEqual(status, 200)
        self.assertIn("geo+json", ctype)
        self.assertEqual(len(json.loads(body)["features"]), 4)

    def test_catalog_reports_emit_count_and_fetched(self):
        catalog = json.loads(self.get("/api/catalog")[2])
        self.assertEqual(catalog["emit_count"], 4)
        self.assertRegex(catalog["emit_fetched"], r"^\d{4}-\d{2}-\d{2}T")

    def test_point_lists_covering_granules_newest_first(self):
        report = json.loads(self.get(self.point_url())[2])
        self.assertEqual([g["id"] for g in report["emit"]], ["near-new", "near-old"])
        self.assertEqual(report["emit"][0]["cloud"], 40.0)

    def test_point_far_from_footprints_has_empty_emit(self):
        url = (f"/api/point?lon={self.lon + 40:.6f}&lat={self.lat:.6f}"
               "&crop=corn&year=2024&cdl_year=2024")
        self.assertEqual(json.loads(self.get(url)[2])["emit"], [])

    def test_point_returns_week_sunday_when_week_given(self):
        report = json.loads(self.get(self.point_url() + "&week=15")[2])
        self.assertEqual(report["week_sunday"], "2024-04-14")

    def test_point_without_week_has_null_week_sunday(self):
        self.assertIsNone(json.loads(self.get(self.point_url())[2])["week_sunday"])

    def test_non_numeric_week_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get(self.point_url() + "&week=abc")
        self.assertEqual(ctx.exception.code, 400)

    def test_out_of_range_week_returns_400(self):
        for bad in (0, 54, 99):
            with self.subTest(week=bad), self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(self.point_url() + f"&week={bad}")
            self.assertEqual(ctx.exception.code, 400)

    def test_week_53_in_a_52_week_year_yields_null_sunday(self):
        report = json.loads(self.get(self.point_url() + "&week=53")[2])
        self.assertIsNone(report["week_sunday"])   # 2024 has 52 ISO weeks

    def test_missing_footprints_file_gives_404_and_empty_emit(self):
        moved = paths.EMIT_FOOTPRINTS.with_name("moved.geojson")
        paths.EMIT_FOOTPRINTS.rename(moved)
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/emit/footprints.geojson")
            self.assertEqual(ctx.exception.code, 404)
            self.assertEqual(json.loads(self.get(self.point_url())[2])["emit"], [])
            self.assertEqual(json.loads(self.get("/api/catalog")[2])["emit_count"], 0)
        finally:
            moved.rename(paths.EMIT_FOOTPRINTS)


class TestEcoRoutes(ServerTestCase):
    def test_eco_footprints_are_served(self):
        status, ctype, body = self.get("/api/eco/footprints.geojson")
        self.assertEqual(status, 200)
        self.assertIn("geo+json", ctype)
        self.assertEqual(len(json.loads(body)["features"]), 3)

    def test_catalog_reports_eco_fields(self):
        catalog = json.loads(self.get("/api/catalog")[2])
        self.assertEqual(catalog["eco_count"], 3)
        self.assertRegex(catalog["eco_fetched"], r"^\d{4}-\d{2}-\d{2}T")
        # near-new (2025-07-20T18:00:00Z) pairs with eco-day 30 s earlier.
        self.assertEqual(catalog["coincident_15min"], 1)

    def test_point_lists_covering_eco_swaths_newest_first(self):
        report = json.loads(self.get(self.point_url())[2])
        self.assertEqual([g["id"] for g in report["eco"]], ["eco-night", "eco-day"])
        self.assertEqual(report["eco"][0]["daynight"], "NIGHT")

    def test_emit_entries_carry_their_pairs(self):
        report = json.loads(self.get(self.point_url())[2])
        near_new = next(g for g in report["emit"] if g["id"] == "near-new")
        self.assertEqual(near_new["eco"][0]["id"], "eco-day")
        self.assertEqual(near_new["eco"][0]["dt"], -30)

    def test_missing_eco_file_gives_404_and_zero_fields(self):
        moved = paths.ECO_FOOTPRINTS.with_name("moved.geojson")
        paths.ECO_FOOTPRINTS.rename(moved)
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/eco/footprints.geojson")
            self.assertEqual(ctx.exception.code, 404)
            catalog = json.loads(self.get("/api/catalog")[2])
            self.assertEqual(catalog["eco_count"], 0)
            self.assertEqual(json.loads(self.get(self.point_url())[2])["eco"], [])
        finally:
            moved.rename(paths.ECO_FOOTPRINTS)


class TestHlsRoutes(ServerTestCase):
    HLS_QUERY = "&start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL&window=7"

    def test_tiles_are_served_as_geojson(self):
        status, ctype, body = self.get("/api/hls/tiles.geojson")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/geo+json")
        tiles = [f["properties"]["tile"] for f in json.loads(body)["features"]]
        self.assertEqual(tiles, ["T98ZZZ", "T99ZZZ"])

    def test_counts_apply_range_cloud_and_sensor(self):
        _, ctype, body = self.get("/api/hls/counts?start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL")
        self.assertEqual(ctype, "application/json")
        self.assertEqual(json.loads(body), {"counts": {"T99ZZZ": 2, "T98ZZZ": 1}})
        _, _, body = self.get("/api/hls/counts?start=2025-07-15&end=2025-07-31&cloud=30&sensor=L30")
        self.assertEqual(json.loads(body), {"counts": {}})
        _, _, body = self.get("/api/hls/counts?start=2025-07-19&end=2025-07-22&cloud=100&sensor=ALL")
        self.assertEqual(json.loads(body), {"counts": {"T99ZZZ": 1, "T98ZZZ": 1}})

    def test_counts_reject_bad_parameters(self):
        for query in ("start=2025-07-15&end=2025-07-31&cloud=30&sensor=X30",
                      "start=2025-7-15&end=2025-07-31&cloud=30&sensor=ALL",
                      "start=2025-07-15&end=2025-07-31&cloud=abc&sensor=ALL",
                      "end=2025-07-31&cloud=30&sensor=ALL"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/hls/counts?" + query)
            self.assertEqual(ctx.exception.code, 400, query)

    def test_catalog_reports_hls_fields(self):
        catalog = json.loads(self.get("/api/catalog")[2])
        self.assertEqual(catalog["hls_count"], 4)
        self.assertEqual(catalog["hls_tiles"], 2)
        self.assertEqual(catalog["hls_fetched"], "2025-08-02T00:00:00+00:00")
        self.assertFalse(catalog["hls_busy"])

    def test_point_lists_covering_tiles_with_clear_counts(self):
        report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        block = report["hls"]
        self.assertEqual((block["start"], block["end"], block["cloud"], block["sensor"], block["window"]),
                         ("2025-07-15", "2025-07-31", 30, "ALL", 7))
        self.assertEqual(len(block["tiles"]), 1)
        tile = block["tiles"][0]
        self.assertEqual(tile["tile"], "T99ZZZ")
        self.assertEqual(tile["clear"], 2)
        self.assertEqual([a["date"] for a in tile["acq"]], ["2025-07-18", "2025-07-21", "2025-07-23"])
        self.assertEqual(tile["acq"][1], {"date": "2025-07-21", "time": "2025-07-21T16:00:00Z",
                                          "sensor": "L30", "cloud": 80})

    def test_point_tags_emit_scenes_with_the_nearest_clear_acquisition(self):
        report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        by_id = {g["id"]: g for g in report["emit"]}
        self.assertEqual(by_id["near-new"]["hls"], {"date": "2025-07-18", "sensor": "S30", "cloud": 10, "dt": -2})
        self.assertIsNone(by_id["near-old"]["hls"])
        # The sensor filter applies to the tag: with S30 excluded only the cloudy L30 row remains.
        report = json.loads(self.get(self.point_url() + "&start=2025-07-15&end=2025-07-31&cloud=30&sensor=L30&window=7")[2])
        self.assertIsNone({g["id"]: g for g in report["emit"]}["near-new"]["hls"])
        self.assertEqual(report["hls"]["tiles"][0]["clear"], 0)

    def test_point_defaults_the_range_to_the_week_window(self):
        # 2024 week 30 ends Sunday 2024-07-28; ±7 days is 07-21 to 08-04.
        report = json.loads(self.get(self.point_url() + "&week=30")[2])
        self.assertEqual((report["hls"]["start"], report["hls"]["end"]), ("2024-07-21", "2024-08-04"))
        self.assertEqual(report["hls"]["tiles"][0]["acq"], [])

    def test_point_payload_omits_the_map_only_pairing_list(self):
        report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        for g in report["emit"]:
            self.assertNotIn("hls_near", g)

    def test_point_outside_every_tile_ring_leaves_emit_scenes_untagged(self):
        # No covering tile means nothing is known about the HLS record here, which
        # is not the same as a covering tile holding no clear acquisition.
        url = (f"/api/point?lon={self.lon + 10:.6f}&lat={self.lat:.6f}"
               "&crop=corn&year=2024&cdl_year=2024" + self.HLS_QUERY)
        report = json.loads(self.get(url)[2])
        self.assertEqual(report["hls"]["tiles"], [])
        self.assertEqual([g["id"] for g in report["emit"]], ["no-tile"])
        for g in report["emit"]:
            self.assertNotIn("hls", g)

    def test_point_without_a_week_has_a_null_range_but_still_tags_scenes(self):
        # No week and no explicit range: the listing has nothing to bound, while
        # the per-scene tags run over each scene's own date.
        report = json.loads(self.get(self.point_url())[2])
        block = report["hls"]
        self.assertEqual((block["start"], block["end"]), (None, None))
        self.assertEqual([t["tile"] for t in block["tiles"]], ["T99ZZZ"])
        self.assertEqual(block["tiles"][0], {"tile": "T99ZZZ", "clear": 0, "acq": []})
        by_id = {g["id"]: g for g in report["emit"]}
        self.assertEqual(by_id["near-new"]["hls"],
                         {"date": "2025-07-18", "sensor": "S30", "cloud": 10, "dt": -2})
        self.assertIsNone(by_id["near-old"]["hls"])

    def test_missing_store_gives_404_null_block_and_zero_fields(self):
        # A tagged request first: if hls_block wrote onto the FootprintIndex's own
        # dicts, the tag would survive into the no-store response below.
        tagged = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
        self.assertIsNotNone({g["id"]: g for g in tagged["emit"]}["near-new"]["hls"])
        moved = paths.HLS_DB.with_name("moved.sqlite")
        paths.HLS_DB.rename(moved)
        try:
            for route in ("/api/hls/tiles.geojson", "/api/hls/counts?start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL"):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.get(route)
                self.assertEqual(ctx.exception.code, 404)
                self.assertIn("run.sh hls", ctx.exception.read().decode())
            report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
            self.assertIsNone(report["hls"])
            for g in report["emit"]:
                self.assertNotIn("hls", g)
            catalog = json.loads(self.get("/api/catalog")[2])
            self.assertEqual((catalog["hls_count"], catalog["hls_tiles"], catalog["hls_fetched"]), (0, 0, None))
            self.assertFalse(catalog["hls_busy"])   # absent is not busy
        finally:
            moved.rename(paths.HLS_DB)


class TestHlsBusyStore(ServerTestCase):
    """A fetch holding the write lock degrades every HLS read; nothing returns 500."""

    HLS_QUERY = "&start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL&window=7"

    def test_a_locked_store_degrades_the_catalog_the_routes_and_the_point(self):
        import sqlite3
        writer = sqlite3.connect(str(paths.HLS_DB), timeout=0.1)
        self.addCleanup(writer.close)
        writer.execute("BEGIN EXCLUSIVE")
        try:
            catalog = json.loads(self.get("/api/catalog")[2])
            self.assertTrue(catalog["hls_busy"])
            self.assertEqual((catalog["hls_count"], catalog["hls_tiles"], catalog["hls_fetched"]),
                             (0, 0, None))
            for route in ("/api/hls/tiles.geojson",
                          "/api/hls/counts?start=2025-07-15&end=2025-07-31&cloud=30&sensor=ALL"):
                with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.get(route)
                self.assertEqual(ctx.exception.code, 503)
                self.assertIn("HLS store busy; a fetch is in progress, retry shortly",
                              ctx.exception.read().decode())
            report = json.loads(self.get(self.point_url() + self.HLS_QUERY)[2])
            self.assertIsNone(report["hls"])
            for g in report["emit"]:
                self.assertNotIn("hls", g)
        finally:
            writer.rollback()
        catalog = json.loads(self.get("/api/catalog")[2])
        self.assertFalse(catalog["hls_busy"])
        self.assertEqual((catalog["hls_count"], catalog["hls_tiles"]), (4, 2))


class TestHlsPairsOnEmit(ServerTestCase):
    def test_catalog_counts_emit_scenes_with_hls_lists(self):
        catalog = json.loads(self.get("/api/catalog")[2])
        self.assertEqual(catalog["emit_hls_paired"], 1)

    def test_emit_features_carry_their_hls_lists(self):
        geo = json.loads(self.get("/api/emit/footprints.geojson")[2])
        by_id = {f["properties"]["id"]: f["properties"] for f in geo["features"]}
        self.assertEqual([(h["date"], h["sensor"], h["cloud"], h["dt"]) for h in by_id["near-new"]["hls_near"]],
                         [("2025-07-21", "L30", 80, 1), ("2025-07-18", "S30", 10, -2), ("2025-07-23", "S30", 10, 3)])
        self.assertEqual(by_id["near-old"]["hls_near"], [])
        self.assertEqual(by_id["no-tile"]["hls_near"], [])


class TestInterfaceAssets(ServerTestCase):
    def test_ecostress_plus_hls_coincidence_mark(self):
        _, _, index = self.get("/")
        html = index.decode()
        self.assertIn('id="coincideAll"', html)
        self.assertIn("Mark ECOSTRESS + HLS coincidence", html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        from viz import hls_pairs
        self.assertIn("HLS_PAIR_DAYS = " + str(hls_pairs.PAIR_DAYS), text)
        self.assertIn("emit_hls_paired", text)
        self.assertIn("purple = ECOSTRESS + HLS coincident", text)
        sync = text[text.index("function syncEmit"):text.index("function drawLegend")]
        self.assertIn("state.days > 0", sync)
        self.assertIn("days of HLS acquisitions", sync)
        style = text[text.index("function emitStyleFor"):text.index("function loadEmit")]
        self.assertIn("hlsPaired(p)", style)
        self.assertIn("HLS_PAIR_FILL", style)
        if not shutil.which("node"):
            self.skipTest("node not available")
        match = re.search(r"function hlsPaired\(p\) \{.*?\n  \}", text, re.S)
        self.assertIsNotNone(match, "hlsPaired function not found in app.js")
        script = (
            'var HLS_PAIR_DAYS = 15; var state = { days: 7, hlsCloud: 30, hlsSensor: "ALL" };\n' + match.group(0) +
            '\nvar near = { hls_near: [{ date: "x", sensor: "S30", cloud: 10, dt: -2 }] };\n'
            'var farOnly = { hls_near: [{ date: "x", sensor: "S30", cloud: 0, dt: 20 }] };\n'
            'var cloudy = { hls_near: [{ date: "x", sensor: "S30", cloud: 80, dt: 1 }, { date: "x", sensor: "L30", cloud: null, dt: 1 }] };\n'
            'var out = [hlsPaired(near), hlsPaired(farOnly), hlsPaired(cloudy), hlsPaired({})];\n'
            'state.days = 30; out.push(hlsPaired(farOnly));\n'
            'state.hlsSensor = "L30"; out.push(hlsPaired(near));\n'
            'state.hlsSensor = "ALL"; state.days = 0; out.push(hlsPaired(near));\n'
            'console.log(out.join("|"));'
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "true|false|false|false|false|false|false")

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
        self.assertIn("&focus=", text)
        self.assertIn("state.mask", text[text.index("function drawCdl"):text.index("function drawCpc")])

    def test_cdl_base_layer_is_toggleable(self):
        _, _, index = self.get("/")
        self.assertIn('id="cdl"', index.decode())
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("state.cdlVisible", text[text.index("function drawCdl"):text.index("function drawCpc")])
        _, _, css = self.get("/static/style.css")
        self.assertIn("#map { position: absolute; inset: 0 340px 0 0; background: #fff;", css.decode())

    def test_app_appends_the_server_token_to_every_tile_url(self):
        _, _, body = self.get("/static/app.js")
        text = body.decode()
        cdl = text[text.index("function drawCdl"):text.index("function drawCpc")]
        cpc = text[text.index("function drawCpc"):text.index("function applyMode")]
        self.assertIn("server_token", cdl)
        self.assertIn("server_token", cpc)

    def test_off_crop_style_switch_exists(self):
        _, _, index = self.get("/")
        html = index.decode()
        self.assertIn('name="dim"', html)
        self.assertIn('value="white"', html)
        self.assertIn('value="grey"', html)
        _, _, app = self.get("/static/app.js")
        self.assertIn("&dim=", app.decode())

    def test_both_layers_have_opacity_sliders(self):
        _, _, index = self.get("/")
        html = index.decode()
        self.assertIn('id="cdlOpacity"', html)
        self.assertIn('id="opacity"', html)
        self.assertIn("CDL opacity", html)
        self.assertIn("CPC opacity", html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("state.cdlOpacity", text[text.index("function drawCdl"):text.index("function drawCpc")])

    def test_map_fits_conus_on_load(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("CONUS_BOUNDS", text)
        self.assertIn("fitBounds(CONUS_BOUNDS", text)
        self.assertIn("zoomSnap: 0.25", text)
        self.assertNotIn("center: [39.5, -96.0], zoom: 4", text)

    def test_cpc_layer_is_recreated_not_reurled_on_week_change(self):
        # Leaflet 1.9.4's GridLayer.redraw(), which setUrl triggers, does not
        # round the map zoom; at a fractional zoom (zoomSnap 0.25) it requests
        # tiles at z=4.75, the server 404s them, and the layer goes blank.
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        body = text[text.index("function drawCpc"):text.index("function applyMode")]
        self.assertNotIn("setUrl(", body)
        self.assertIn("map.removeLayer(cpcLayer)", body)

    def test_emit_within_window_only_toggle(self):
        _, _, index = self.get("/")
        self.assertIn('id="emitOnlyWindow"', index.decode())
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        body = text[text.index("function emitStyleFor"):text.index("function loadEmit")]
        self.assertIn("state.emitOnlyWindow", body)

    def test_ecostress_layer_and_coincidence_controls(self):
        _, _, index = self.get("/")
        html = index.decode()
        for ident in ('id="eco"', 'name="ecoDay"', 'id="coincide"', 'id="coincideWindow"',
                      'id="coincideOnly"', 'id="ecoLegend"'):
            self.assertIn(ident, html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("/api/eco/footprints.geojson", text)
        self.assertIn("COINCIDE_STEPS", text)
        self.assertIn("[1, 5, 15, 30, 60, 120, 360, 720, 1440]", text)
        self.assertIn('dashArray: "4 4"', text)
        self.assertIn("fillOpacity", text[text.index("function emitStyleFor"):text.index("function loadEmit")])
        self.assertIn("ECOSTRESS swaths covering this point", text)

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

    def test_emit_layer_controls_and_canvas_renderer(self):
        _, _, index = self.get("/")
        html = index.decode()
        for ident in ('id="emit"', 'id="emitCloud"', 'id="timeWindow"', 'id="emitLegend"'):
            self.assertIn(ident, html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("/api/emit/footprints.geojson", text)
        self.assertIn("L.canvas(", text)
        self.assertIn("EMIT_YEAR_COLOURS", text)
        self.assertIn("&week=", text)
        self.assertIn("EMIT scenes covering this point", text)

        if not shutil.which("node"):
            self.skipTest("node not available")
        match = re.search(r"function weekSunday\(year, week\) \{.*?\n  \}", text, re.S)
        self.assertIsNotNone(match, "weekSunday function not found in app.js")
        script = match.group(0) + "\nconsole.log(weekSunday(2024, 15).toISOString().slice(0, 10));"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "2024-04-14")

    def test_format_dt_rounds_to_whole_minutes_before_splitting_hours(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        if not shutil.which("node"):
            self.skipTest("node not available")
        match = re.search(r"function formatDt\(seconds\) \{.*?\n  \}", text, re.S)
        self.assertIsNotNone(match, "formatDt function not found in app.js")
        script = (match.group(0) +
                  "\nconsole.log([7199, 3599, -41, 45, 86400].map(formatDt).join(\"|\"));")
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "+2 h 0 m|+1 h 0 m|−41 s|+45 s|+24 h 0 m")

    def test_time_window_is_shared_above_the_emit_block(self):
        _, _, index = self.get("/")
        html = index.decode()
        self.assertIn('id="timeWindow"', html)
        self.assertNotIn('id="emitWindow"', html)
        self.assertLess(html.index('id="timeWindow"'), html.index('id="emit"'))
        self.assertIn("Time window", html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("function windowBounds()", text)
        self.assertNotIn("emitWindowBounds", text)
        self.assertIn("state.days", text)

    def test_hls_layer_controls_and_routes(self):
        _, _, index = self.get("/")
        html = index.decode()
        for ident in ('id="hls"', 'name="hlsMode"', 'id="hlsRange"', 'id="hlsCloud"',
                      'name="hlsSensor"', 'id="hlsLegend"'):
            self.assertIn(ident, html)
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("/api/hls/tiles.geojson", text)
        self.assertIn("/api/hls/counts?", text)
        self.assertIn("HLS_CLASSES", text)
        for colour in ("#e7d4e8", "#c2a5cf", "#9970ab", "#762a83", "#40004b"):
            self.assertIn(colour, text)
        self.assertIn('map.createPane("hls")', text)
        self.assertIn("451", text[text.index('map.getPane("hls")'):text.index('map.getPane("hls")') + 80])
        self.assertIn("setTimeout(fetchHlsCounts, 150)", text)

    def test_hls_range_resolves_week_window_and_season(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        if not shutil.which("node"):
            self.skipTest("node not available")
        parts = []
        for name in ("weekSunday(year, week)", "isoDate(ms)", "hlsRange()"):
            match = re.search(r"function " + re.escape(name) + r" \{.*?\n  \}", text, re.S)
            self.assertIsNotNone(match, name + " not found in app.js")
            parts.append(match.group(0))
        script = (
            'var state = { week: 30, year: 2025, days: 7, hlsMode: "week", crop: "corn", var: "cond" };\n'
            "function weeksFor() { return [14, 30, 44]; }\n" + "\n".join(parts) + "\n"
            "var a = hlsRange(); state.hlsMode = \"season\"; var b = hlsRange();\n"
            "state.days = 0; state.hlsMode = \"week\"; var c = hlsRange();\n"
            "state.week = null; var d = hlsRange();\n"
            "console.log([a.start, a.end, b.start, b.end, c.start, c.end, String(d)].join(\"|\"));"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         "2025-07-20|2025-08-03|2025-03-31|2025-11-02|2025-07-27|2025-07-27|null")

    def test_catalog_boot_fetch_reports_a_failed_request(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        boot = text[text.index('fetch("/api/catalog")'):]
        self.assertIn(".catch(", boot)
        self.assertIn("Catalog request failed; is the server running? Reload to retry.", boot)
        self.assertIn("hls_busy", text)

    def test_hls_layer_is_gated_on_tile_rings_as_well_as_rows(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        body = text[text.index("function loadHls"):text.index("function syncEco")]
        self.assertIn("hls_tiles", body)
        self.assertIn("HLS tile rings not fetched yet; let ./run.sh hls finish.", body)
        self.assertIn("HLS store busy; a fetch is in progress. Reload when it finishes.", body)
        self.assertIn("g.hls === null", text)
        self.assertIn("no week selected", text)

    def test_readout_lists_are_collapsible_and_remembered(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("function foldable(key, heading, body)", text)
        self.assertIn('<details class="fold" data-fold=', text)
        self.assertIn('<summary class="section">', text)
        for key in ('foldable("emit"', 'foldable("eco"', 'foldable("hls"'):
            self.assertIn(key, text)
        self.assertIn('addEventListener("toggle"', text)
        # A re-render on toggle would rebuild the section closed; only the layout is redone.
        wiring = text[text.index("function wireFolds"):text.index("function showReadout")]
        self.assertNotIn("popup.update()", wiring)
        for step in ("popup._updateLayout()", "popup._updatePosition()", "popup._adjustPan()"):
            self.assertIn(step, wiring)
        self.assertIn("readoutOpen[details.dataset.fold] = details.open", text)
        self.assertIn("maxHeight: Math.round(map.getSize().y * 0.6)", text)
        _, _, css = self.get("/static/style.css")
        self.assertIn("details.fold[open]", css.decode())
        self.assertIn("leaflet-popup-scrolled", css.decode())

    def test_readout_lists_hls_acquisitions_and_tags_emit_scenes(self):
        _, _, app = self.get("/static/app.js")
        text = app.decode()
        self.assertIn("HLS acquisitions", text)
        self.assertIn("clear of", text)
        self.assertIn("no clear HLS within", text)
        self.assertIn("&start=", text)
        self.assertIn("&window=", text)
        self.assertIn("slice(0, 20)", text)
        if not shutil.which("node"):
            self.skipTest("node not available")
        match = re.search(r"function formatDays\(dt\) \{.*?\n  \}", text, re.S)
        self.assertIsNotNone(match, "formatDays function not found in app.js")
        script = match.group(0) + "\nconsole.log([0, -2, 3, 1].map(formatDays).join(\"|\"));"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "same day|−2 d|+3 d|+1 d")


if __name__ == "__main__":
    unittest.main()
