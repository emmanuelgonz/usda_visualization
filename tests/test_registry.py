import json
import tempfile
import unittest
from pathlib import Path

from viz import paths, registry

VALID = {
    "version": 1,
    "region": {"name": "Test", "bbox": [-100.0, 40.0, -99.0, 41.0]},
    "missions": [
        {"key": "sw", "name": "Swath", "label": "Swath scenes", "archetype": "swath", "footprint": "polygon",
         "cmr": [{"short_name": "SW", "version": "1"}], "since": "2024-01",
         "attributes": {"cloud": {"from": "cloud_cover", "type": "number"}},
         "filters": [{"attribute": "cloud", "control": "max", "default": 30, "label": "Max cloud"}],
         "browse": {"source": "links", "match": "\\.png$"}, "links": {}, "style": {"colour": "#123456"}},
        {"key": "ti", "name": "Tiled", "label": "Tiled coverage", "archetype": "tiled", "grid": "mgrs",
         "cmr": [{"short_name": "TA", "version": "2.0", "implies": {"sensor": "A"}}], "since": "2024-01",
         "tile_from": {"field": "title", "pattern": "^TI\\.(T[0-9]{2}[A-Z]{3})\\."},
         "attributes": {"sensor": {"from": "sensor", "type": "text"}},
         "filters": [{"attribute": "sensor", "control": "choice", "values": ["ALL", "A"], "default": "ALL", "label": "Sensor"}],
         "browse": {"source": "links", "match": "\\.jpg$"}, "links": {}, "style": {}},
    ],
}


def write(tmp, data):
    path = Path(tmp) / "missions.json"
    path.write_text(json.dumps(data))
    return path


class TestRealFile(unittest.TestCase):
    def test_real_registry_loads_with_three_missions(self):
        reg = registry.load(paths.MISSIONS)
        self.assertEqual(list(reg.missions), ["emit", "eco", "hls"])
        self.assertEqual(reg.region.name, "CONUS")
        self.assertEqual(reg.region.bbox, (-125.0, 24.4, -66.9, 49.4))
        self.assertEqual([m.key for m in reg.tiled()], ["hls"])
        self.assertEqual([m.key for m in reg.swath()], ["emit", "eco"])
        hls = reg.mission("hls")
        self.assertEqual(hls.tile_from.pattern.match("HLS.S30.T15TVH.2025203T170849.v2.0").group(1), "T15TVH")
        self.assertEqual(hls.cmr[0].implies, {"sensor": "L30"})
        self.assertEqual(hls.attributes["cloud"].type, "int")
        self.assertEqual(reg.mission("eco").footprint, "box")
        self.assertEqual(reg.mission("emit").filters[0].default, 30)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def load(self, mutate):
        data = json.loads(json.dumps(VALID))
        mutate(data)
        return registry.load(write(self.tmp, data))

    def test_valid_fixture_loads(self):
        reg = self.load(lambda d: None)
        self.assertEqual(reg.mission("ti").attributes["sensor"].source, "sensor")
        with self.assertRaises(KeyError):
            reg.mission("nope")

    def test_duplicate_key_names_the_mission(self):
        with self.assertRaisesRegex(registry.RegistryError, "sw"):
            self.load(lambda d: d["missions"].append(dict(d["missions"][0])))

    def test_unknown_archetype_grid_footprint_control_and_source(self):
        for mutate, word in (
            (lambda d: d["missions"][0].update(archetype="orbit"), "archetype"),
            (lambda d: d["missions"][1].update(grid="hex"), "grid"),
            (lambda d: d["missions"][0].update(footprint="circle"), "footprint"),
            (lambda d: d["missions"][0]["filters"][0].update(control="range"), "control"),
            (lambda d: d["missions"][0]["browse"].update(source="ftp"), "browse"),
        ):
            with self.assertRaisesRegex(registry.RegistryError, word):
                self.load(mutate)

    def test_bad_pattern_and_filter_on_undeclared_attribute(self):
        with self.assertRaisesRegex(registry.RegistryError, "ti"):
            self.load(lambda d: d["missions"][1]["tile_from"].update(pattern="^TI\\.(T["))
        with self.assertRaisesRegex(registry.RegistryError, "cloud"):
            self.load(lambda d: d["missions"][1]["filters"].append(
                {"attribute": "cloud", "control": "max", "default": 1, "label": "x"}))

    def test_tile_from_pattern_needs_a_group(self):
        with self.assertRaisesRegex(registry.RegistryError, "group"):
            self.load(lambda d: d["missions"][1]["tile_from"].update(pattern="^TI\\.T[0-9]{2}[A-Z]{3}\\."))

    def test_since_must_be_a_month_and_tiled_needs_grid_and_tile_from(self):
        with self.assertRaisesRegex(registry.RegistryError, "since"):
            self.load(lambda d: d["missions"][0].update(since="2024-1"))
        with self.assertRaisesRegex(registry.RegistryError, "tile_from"):
            self.load(lambda d: d["missions"][1].pop("tile_from"))
        with self.assertRaisesRegex(registry.RegistryError, "footprint"):
            self.load(lambda d: d["missions"][0].pop("footprint"))

    def test_sibling_id_pattern_needs_enough_groups(self):
        sibling = {"source": "sibling", "short_name": "X", "version": "1", "id_pattern": "^(\\d+)_",
                   "sibling_pattern": "{0}_{1}_*", "kinds": {}}
        with self.assertRaisesRegex(registry.RegistryError, "sw.*capture group"):
            self.load(lambda d: d["missions"][0].update(browse=sibling))
        sibling["id_pattern"] = "^(\\d+)_(\\d+)"
        self.load(lambda d: d["missions"][0].update(browse=sibling))

    def test_missing_fields_raise_registry_errors(self):
        with self.assertRaisesRegex(registry.RegistryError, "sw.*short_name"):
            self.load(lambda d: d["missions"][0]["cmr"][0].pop("short_name"))
        with self.assertRaisesRegex(registry.RegistryError, "sw.*version"):
            self.load(lambda d: d["missions"][0]["cmr"][0].pop("version"))
        with self.assertRaisesRegex(registry.RegistryError, "sw.*cloud.*from"):
            self.load(lambda d: d["missions"][0]["attributes"]["cloud"].pop("from"))
        with self.assertRaisesRegex(registry.RegistryError, "region.*bbox"):
            self.load(lambda d: d["region"].update(bbox=[-100, 40, "x", 41]))

    def test_choice_default_must_be_a_value(self):
        with self.assertRaisesRegex(registry.RegistryError, "default"):
            self.load(lambda d: d["missions"][1]["filters"][0].update(default="Z"))


class TestPaths(unittest.TestCase):
    def test_catalog_db_is_lowercase_region_under_data_catalog(self):
        self.assertEqual(paths.catalog_db("CONUS"), paths.CATALOG_DATA / "conus.sqlite")
        self.assertEqual(paths.CATALOG_DATA, paths.DATA / "catalog")
        self.assertEqual(paths.MISSIONS, paths.ROOT / "viz" / "missions.json")
