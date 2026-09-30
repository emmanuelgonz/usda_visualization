import io
import json
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from viz import catalog, refresh, registry
from tests.test_archetype_swath import POLY, REG as SWATH_REG
from tests.test_archetype_tiled import HEADER, MISSION as HLS, row as csv_row

NOW = lambda: "2025-08-15T00:00:00+00:00"


class TestRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cat = catalog.Catalog(self.tmp / "c.sqlite")
        self.out = io.StringIO()

    def tearDown(self):
        self.cat.close()
        shutil.rmtree(self.tmp)

    def test_tiled_mission_fetches_each_collection_month_and_skips_frozen(self):
        reg = registry.Registry(SWATH_REG.region, [HLS])
        calls = []

        def fake_csv(url):
            calls.append(url)
            if "HLSS30" in url and "2025-07" in url:
                return (HEADER + csv_row("HLS.S30.T15TVH.2025199T170000.v2.0", "2025-07-18T17:00:00Z", 10)).encode(), {"CMR-Hits": "1"}
            return HEADER.encode(), {"CMR-Hits": "0"}

        self.cat.replace_month("hls", "2025-06", [], "2025-08-01T00:00:00+00:00")   # 31 days after month end: not frozen
        self.cat.replace_month("hls", "2025-05", [], "2025-08-01T00:00:00+00:00")   # 62 days after: frozen
        with unittest.mock.patch("viz.months.current_month", return_value="2025-07"):
            counts = refresh.refresh_mission(self.cat, reg, HLS, first="2025-05", fetch_csv=fake_csv, out=self.out, now=NOW)
        months_asked = sorted({u.rsplit("temporal=", 1)[1][:7] for u in calls})
        self.assertEqual(months_asked, ["2025-06", "2025-07"])
        self.assertEqual(counts["months"], 2)
        self.assertEqual(counts["granules"], 1)
        self.assertEqual(self.cat.fetched_at("hls", "2025-07"), NOW())
        self.assertIn("hls 2025-07: 1 granules", self.out.getvalue())

    def test_failed_month_keeps_previous_rows(self):
        reg = registry.Registry(SWATH_REG.region, [HLS])
        self.cat.replace_month("hls", "2025-07", [{"id": "old", "start": "2025-07-01T00:00:00Z", "tile": "T15TVH"}], "2025-07-20T00:00:00+00:00")

        def failing(url):
            raise OSError("cmr down")

        with unittest.mock.patch("viz.months.current_month", return_value="2025-07"):
            with self.assertRaises(OSError):
                refresh.refresh_mission(self.cat, reg, HLS, first="2025-07", fetch_csv=failing, out=self.out, now=NOW)
        self.assertEqual([r[0] for r in self.cat.conn.execute("SELECT id FROM granules")], ["old"])
        self.assertEqual(self.cat.fetched_at("hls", "2025-07"), "2025-07-20T00:00:00+00:00")

    def test_finish_grids_computes_rings_and_coverage(self):
        reg = registry.Registry(SWATH_REG.region, [SWATH_REG.mission("emit"), HLS])
        self.cat.replace_month("hls", "2025-07", [{"id": "t", "start": "2025-07-01T00:00:00Z", "tile": "T15TVH", "sensor": "S30"}], "x")
        from viz.archetypes import swath
        emit = SWATH_REG.mission("emit")
        iowa = dict(POLY, polygons=[["41.5 -94 41.5 -93 42.5 -93 42.5 -94 41.5 -94"]])
        self.cat.replace_month("emit", "2025-07", [swath.entry_to_row(emit, emit.cmr[0], iowa)], "x")
        refresh.finish_grids(self.cat, reg, out=self.out)
        self.assertEqual(self.cat.known_tiles("mgrs"), {"T15TVH"})
        self.assertEqual([r[0] for r in self.cat.conn.execute("SELECT tile FROM coverage WHERE mission='emit'")], ["T15TVH"])
        self.assertIn("mgrs: 1 tile outlines", self.out.getvalue())
        self.assertIn("emit coverage: 1 rows", self.out.getvalue())

    def test_main_runs_named_missions_against_a_registry_and_catalog_path(self):
        reg_path = self.tmp / "m.json"
        reg_path.write_text(json.dumps({
            "region": {"name": "Test", "bbox": [-125.0, 24.4, -66.9, 49.4]},
            "missions": [{"key": "emit", "name": "EMIT", "label": "EMIT", "archetype": "swath", "footprint": "polygon",
                          "cmr": [{"short_name": "EMITL2ARFL", "version": "001"}], "since": "2025-07",
                          "attributes": {}, "filters": [], "browse": {"source": "links", "match": "\\.png$"},
                          "links": {}, "style": {}}]}))
        pages = {1: [POLY], 2: []}
        with unittest.mock.patch("viz.months.current_month", return_value="2025-07"), \
             unittest.mock.patch("viz.cmr.fetch_page", side_effect=lambda url: pages[int(url.rsplit("page_num=", 1)[1])]), \
             unittest.mock.patch("sys.stdout", self.out):
            code = refresh.main(["emit", "--registry", str(reg_path), "--catalog", str(self.tmp / "r.sqlite")])
        self.assertEqual(code, 0)
        cat = catalog.open_read_only(self.tmp / "r.sqlite")
        try:
            self.assertEqual(cat.summary("emit")["count"], 1)
        finally:
            cat.close()

    def test_unknown_mission_is_an_error(self):
        with unittest.mock.patch("sys.stderr", io.StringIO()) as err:
            code = refresh.main(["nope", "--catalog", str(self.tmp / "r.sqlite")])
        self.assertEqual(code, 2)
        self.assertIn("nope", err.getvalue())
