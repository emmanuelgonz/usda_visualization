import subprocess
import unittest
from pathlib import Path

from viz import paths


class TestRunSh(unittest.TestCase):
    def test_run_sh_has_refresh_and_migrate_cases_and_parses(self):
        text = (paths.ROOT / "run.sh").read_text()
        self.assertIn("  refresh)", text)
        self.assertIn("  migrate)", text)
        self.assertIn("python3 -m viz.refresh", text)
        self.assertIn("python3 -m viz.migrate", text)
        self.assertIn("data/catalog/conus.sqlite", text)
        self.assertIn("refresh|migrate", text)
        self.assertEqual(subprocess.run(["bash", "-n", str(paths.ROOT / "run.sh")]).returncode, 0)

    def test_catalog_directory_is_ignored(self):
        ignore = (paths.ROOT / ".gitignore").read_text().splitlines()
        self.assertTrue(any(line.strip() in ("data/", "data", "/data/", "/data") or line.strip().startswith("data/catalog") for line in ignore))
