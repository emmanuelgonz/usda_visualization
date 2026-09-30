import shutil
import tempfile
import unittest
from pathlib import Path

from viz import catalog, registry
from viz.archetypes import swath

BBOX = (-125.0, 24.4, -66.9, 49.4)
REG = registry.parse({
    "region": {"name": "T", "bbox": list(BBOX)},
    "missions": [
        {"key": "emit", "name": "EMIT", "label": "EMIT", "archetype": "swath", "footprint": "polygon",
         "cmr": [{"short_name": "EMITL2ARFL", "version": "001"}], "since": "2022-08",
         "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}},
         "filters": [], "browse": {"source": "links", "match": "\\.png$"},
         "links": {"data": {"rel": "data#", "match": "\\.nc$"}}, "style": {}},
        {"key": "eco", "name": "ECOSTRESS", "label": "ECO", "archetype": "swath", "footprint": "box",
         "cmr": [{"short_name": "ECO_L2_LSTE", "version": "002"}], "since": "2022-01",
         "attributes": {"daynight": {"from": "day_night_flag", "type": "text"}, "orbit": {"from": "orbit", "type": "int"}},
         "filters": [], "browse": {"source": "sibling", "short_name": "X", "version": "1", "id_pattern": "^(\\d+)_(\\d+)",
                                   "sibling_pattern": "{0}_{1}", "kinds": {}}, "links": {}, "style": {}},
    ]})
EMIT, ECO = REG.mission("emit"), REG.mission("eco")

POLY = {"title": "EMIT_1", "time_start": "2025-07-02T16:00:00.000Z", "time_end": "2025-07-02T16:00:12.000Z",
        "cloud_cover": "12", "polygons": [["40 -100 40 -99 41 -99 41 -100 40 -100"]],
        "links": [{"rel": "http://esipfed.org/ns/fedsearch/1.1/browse#", "href": "https://x/EMIT_1.png"},
                  {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "s3://x/EMIT_1.nc"},
                  {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://x/EMIT_1.nc"}]}
BOX = {"title": "ECO_1", "time_start": "2025-07-02T16:03:00.000Z", "time_end": "2025-07-02T16:03:52.000Z",
       "day_night_flag": "DAY", "boxes": ["39 -101 42 -98"],
       "orbit_calculated_spatial_domains": [{"start_orbit_number": "39607"}], "links": []}


class TestEntries(unittest.TestCase):
    def test_polygon_entry_to_row(self):
        row = swath.entry_to_row(EMIT, EMIT.cmr[0], POLY)
        self.assertEqual(row["id"], "EMIT_1")
        self.assertEqual(row["ring"], [[-100, 40], [-99, 40], [-99, 41], [-100, 41], [-100, 40]])
        self.assertEqual((row["minlon"], row["minlat"], row["maxlon"], row["maxlat"]), (-100, 40, -99, 41))
        self.assertEqual(row["cloud"], 12.0)
        self.assertEqual(row["browse"], "https://x/EMIT_1.png")
        self.assertEqual(row["data"], "https://x/EMIT_1.nc")
        self.assertEqual(row["start"], "2025-07-02T16:00:00.000Z")

    def test_box_entry_to_row_with_orbit(self):
        row = swath.entry_to_row(ECO, ECO.cmr[0], BOX)
        self.assertEqual(row["ring"], [[-101, 39], [-98, 39], [-98, 42], [-101, 42], [-101, 39]])
        self.assertEqual((row["daynight"], row["orbit"], row["browse"]), ("DAY", 39607, None))

    def test_degenerate_polygon_is_skipped(self):
        self.assertIsNone(swath.entry_to_row(EMIT, EMIT.cmr[0], dict(POLY, polygons=[["40 -100 41 -99"]])))
        self.assertIsNone(swath.entry_to_row(EMIT, EMIT.cmr[0], dict(POLY, polygons=[])))
        self.assertIsNone(swath.entry_to_row(EMIT, EMIT.cmr[0], dict(POLY, time_start=None)))
        unclosed = dict(POLY, polygons=[["40 -100 40 -99 41 -99 41 -100"]])
        self.assertEqual(swath.entry_to_row(EMIT, EMIT.cmr[0], unclosed)["ring"][-1], [-100, 40])


class TestFetchMonth(unittest.TestCase):
    def test_pages_until_empty_and_filters_to_the_month(self):
        pages = {1: [POLY, dict(POLY, title="EMIT_2", time_start="2025-08-01T00:00:00Z")], 2: []}
        asked = []

        def fake(url):
            page = int(url.rsplit("page_num=", 1)[1])
            asked.append(url)
            return pages[page]

        rows = swath.fetch_month(EMIT, EMIT.cmr[0], BBOX, "2025-07", fetch_fn=fake)
        self.assertEqual([r["id"] for r in rows], ["EMIT_1"])
        self.assertEqual(len(asked), 2)
        self.assertIn("temporal=2025-07-01T00:00:00Z,2025-08-01T00:00:00Z", asked[0])
        self.assertIn("short_name=EMITL2ARFL", asked[0])
        self.assertIn("version=001", asked[0])
        self.assertIn("bounding_box=-125.0,24.4,-66.9,49.4", asked[0])


class TestCoverage(unittest.TestCase):
    def test_coverage_rows_for_uncovered_granules(self):
        tmp = Path(tempfile.mkdtemp())
        cat = catalog.Catalog(tmp / "c.sqlite")
        try:
            cat.replace_month("emit", "2025-07", [swath.entry_to_row(EMIT, EMIT.cmr[0], POLY)], "x")
            cat.put_tiles("mgrs", [("A", [[-100.5, 39.5], [-99.5, 39.5], [-99.5, 40.5], [-100.5, 40.5], [-100.5, 39.5]]),
                                   ("B", [[-98, 40], [-97, 40], [-97, 41], [-98, 41], [-98, 40]]),
                                   ("C", [[-99.2, 40.8], [-98.9, 40.8], [-98.9, 41.2], [-99.2, 41.2], [-99.2, 40.8]])])
            self.assertEqual(swath.compute_coverage(cat, EMIT, "mgrs"), 2)     # A overlaps, C overlaps the corner, B is clear
            tiles = [r[0] for r in cat.conn.execute("SELECT tile FROM coverage WHERE id='EMIT_1' ORDER BY tile")]
            self.assertEqual(tiles, ["A", "C"])
            self.assertEqual(swath.compute_coverage(cat, EMIT, "mgrs"), 0)     # nothing left uncovered
        finally:
            cat.close()
            shutil.rmtree(tmp)
