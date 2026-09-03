# USDA CPC over CDL Visualization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve USDA Crop Progress and Condition (CPC) gridded weekly layers over the USDA Cropland Data Layer (CDL) in a local browser, reading every byte from local disk.

**Architecture:** A one-time extraction step flattens both zip archives to disk, a preparation step builds a catalog and precomputes two-band crop-fraction masks on the 9 km CPC grid, and a standard-library HTTP server warps EPSG:5070 to EPSG:3857 per 256×256 tile on demand, colorizes through NumPy lookup tables, and caches PNGs to disk. A vendored Leaflet page consumes those tiles.

**Tech Stack:** Python 3.10 standard library (`http.server`, `zipfile`, `unittest`), GDAL 3.8.4 Python bindings, NumPy 1.21.5, Leaflet 1.9.4 (vendored). No `pip install` at any point.

**Spec:** `docs/superpowers/specs/2026-09-03-usda-cpc-cdl-viz-design.md`

## Global Constraints

- **Every Python invocation must set `PYTHONNOUSERSITE=1`.** System GDAL bindings are compiled against NumPy 1.x; NumPy 2.2.4 in the user site directory shadows the system NumPy 1.21.5 and breaks `osgeo.gdal_array`. This applies to running the server, the scripts, and the tests.
- **No `pip install`, no virtualenv, no change to the global Python environment.** Only `numpy`, `osgeo.gdal`, `osgeo.gdal_array`, and the standard library are available.
- **No network access at view time.** Leaflet is vendored to `viz/web/vendor/leaflet/`; the only network use in the whole project is the one-time vendoring step in Task 8.
- **Never add attribution to any commit message.** No `Co-Authored-By`, no "Generated with Claude Code", no mention of Claude, Anthropic, or an AI assistant. Git identity is `emmanuelgonz <emmanuelgonzalez@asu.edu>` and is already configured globally.
- Project root: `/mnt/c/Users/emgonz38/Downloads/usda`. All paths below are relative to it.
- Source archives in `usda_crop_progress_and_condition_gridded_layers/` and `usda_cropland_data_layer/` are **read-only** and must never be modified or deleted.
- CDL 10 m products (`2024_10m_cdls.zip`, `2025_10m_cdls.zip`) are out of scope.
- **Crop-fraction masks are built for CDL 2024 and 2025 only.** All four CDL years are extracted and serve as base layers; only these two get masks, because a full-resolution mask pass costs on the order of an hour per year. The catalog records which years have masks and the interface disables the mask control for years that do not, so 2022 and 2023 can be added later by running `./run.sh prepare --year 2022 --year 2023` with no code change.
- Canonical CPC grid: 508 × 320, origin `(-2309800.213402342982590, 3185470.286793155129999)`, pixel size `8999.255456289853100` × `-8995.486488541766448`, EPSG:5070.
- Condition ramp domain is fixed at **1.0 to 5.0**; progress ramp domain is fixed at **0.0 to 1.0**. Both are identical across every week, crop, and year.
- CDL class 0 (Background) and CPC NoData (`-9999`) always render fully transparent.

## File Structure

| Path | Responsibility |
| --- | --- |
| `viz/paths.py` | Project directory constants; the single place any path is defined |
| `viz/naming.py` | Parsing and formatting of CPC filenames; crop code sets; year pairing |
| `viz/extract.py` | Idempotent extraction of both archives (Task 2) |
| `viz/gridmath.py` | Web Mercator tile arithmetic; canonical CPC grid constants |
| `viz/color.py` | Lookup table construction for the CDL palette and both CPC ramps |
| `viz/rasters.py` | Thread-local GDAL dataset cache; warp-to-tile; point sampling |
| `viz/prepare.py` | Catalog assembly and crop-fraction mask construction |
| `viz/tileserver.py` | HTTP request routing, tile cache, JSON endpoints, `__main__` |
| `viz/web/index.html` | Leaflet interface markup |
| `viz/web/app.js` | Interface behavior: controls, modes, readout, sparkline |
| `viz/web/style.css` | Interface styling |
| `viz/web/vendor/leaflet/` | Vendored Leaflet 1.9.4 |
| `tests/test_naming.py` | Task 1 |
| `tests/test_gridmath.py` | Task 3 |
| `tests/test_color.py` | Task 4 |
| `tests/test_rasters.py` | Task 5 |
| `tests/test_prepare.py` | Task 6 |
| `tests/test_server.py` | Task 7 |
| `tests/fixtures.py` | Synthetic small rasters shared by Tasks 5–7 |
| `run.sh` | One-line launcher setting `PYTHONNOUSERSITE=1` |

Tasks 1, 3, and 4 have no GDAL I/O and are pure-function modules, so they are fast to test and are built first. Task 2 (extraction) is slow but independent, so it runs early and its output feeds Tasks 5–7. Task 6 depends on Task 2 having produced real CDL files.

---

### Task 1: Naming, crop codes, and year pairing

**Files:**
- Create: `viz/__init__.py`, `viz/paths.py`, `viz/naming.py`
- Test: `tests/test_naming.py`, `tests/__init__.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `paths.ROOT: Path`, `paths.CPC_ARCHIVES: Path`, `paths.CDL_ARCHIVES: Path`, `paths.DATA: Path`, `paths.CPC_DATA: Path`, `paths.CDL_DATA: Path`, `paths.MASK_DATA: Path`, `paths.CATALOG: Path`, `paths.CACHE: Path`, `paths.WEB: Path`
  - `naming.CROPS: tuple[str, ...]` = `("corn", "cotton", "soy", "wheat")`
  - `naming.VARS: tuple[str, ...]` = `("cond", "prog")`
  - `naming.CROP_CODES: dict[str, dict[str, tuple[int, ...]]]`
  - `naming.parse_cpc_filename(name: str) -> dict | None` returning keys `crop`, `var`, `year`, `week`
  - `naming.cpc_relpath(crop: str, var: str, year: int, week: int) -> str`
  - `naming.pair_cdl_year(cpc_year: int, cdl_years: Sequence[int]) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/__init__.py` as an empty file, and `tests/test_naming.py`:

```python
import unittest

from viz import naming


class TestParseCpcFilename(unittest.TestCase):
    def test_parses_condition_filename(self):
        self.assertEqual(
            naming.parse_cpc_filename("cornCond24w15.tif"),
            {"crop": "corn", "var": "cond", "year": 2024, "week": 15},
        )

    def test_parses_progress_filename(self):
        self.assertEqual(
            naming.parse_cpc_filename("cottonProg16w42.tif"),
            {"crop": "cotton", "var": "prog", "year": 2016, "week": 42},
        )

    def test_parses_two_digit_week(self):
        self.assertEqual(naming.parse_cpc_filename("soyProg26w09.tif")["week"], 9)

    def test_rejects_non_matching_names(self):
        for bad in ("readme.txt", "cornCond24.tif", "corn_Cond24w15.tif", "cornCond24w15.tfw"):
            with self.subTest(bad=bad):
                self.assertIsNone(naming.parse_cpc_filename(bad))


class TestCpcRelpath(unittest.TestCase):
    def test_round_trips_with_parser(self):
        rel = naming.cpc_relpath("wheat", "cond", 2019, 7)
        self.assertEqual(rel, "wheat/cond/wheatCond19w07.tif")
        self.assertEqual(
            naming.parse_cpc_filename(rel.split("/")[-1]),
            {"crop": "wheat", "var": "cond", "year": 2019, "week": 7},
        )


class TestCropCodes(unittest.TestCase):
    def test_every_crop_has_both_sets(self):
        self.assertEqual(set(naming.CROP_CODES), set(naming.CROPS))
        for crop, sets in naming.CROP_CODES.items():
            with self.subTest(crop=crop):
                self.assertEqual(set(sets), {"primary", "double"})

    def test_primary_codes_match_spec(self):
        self.assertEqual(naming.CROP_CODES["corn"]["primary"], (1,))
        self.assertEqual(naming.CROP_CODES["cotton"]["primary"], (2,))
        self.assertEqual(naming.CROP_CODES["soy"]["primary"], (5,))
        self.assertEqual(naming.CROP_CODES["wheat"]["primary"], (22, 23, 24))

    def test_double_crop_codes_count_toward_both_constituents(self):
        # 26 = Dbl Crop WinWht/Soybeans
        self.assertIn(26, naming.CROP_CODES["soy"]["double"])
        self.assertIn(26, naming.CROP_CODES["wheat"]["double"])
        # 225 = Dbl Crop WinWht/Corn
        self.assertIn(225, naming.CROP_CODES["corn"]["double"])
        self.assertIn(225, naming.CROP_CODES["wheat"]["double"])
        # 241 = Dbl Crop Corn/Soybeans
        self.assertIn(241, naming.CROP_CODES["corn"]["double"])
        self.assertIn(241, naming.CROP_CODES["soy"]["double"])
        # 239 = Dbl Crop Soybeans/Cotton
        self.assertIn(239, naming.CROP_CODES["soy"]["double"])
        self.assertIn(239, naming.CROP_CODES["cotton"]["double"])

    def test_excluded_classes_appear_nowhere(self):
        excluded = {12, 13, 39}  # Sweet Corn, Pop or Orn Corn, Buckwheat
        for crop, sets in naming.CROP_CODES.items():
            for kind, codes in sets.items():
                with self.subTest(crop=crop, kind=kind):
                    self.assertEqual(excluded & set(codes), set())

    def test_primary_and_double_never_overlap_within_a_crop(self):
        for crop, sets in naming.CROP_CODES.items():
            with self.subTest(crop=crop):
                self.assertEqual(set(sets["primary"]) & set(sets["double"]), set())

    def test_all_codes_are_valid_byte_values(self):
        for sets in naming.CROP_CODES.values():
            for codes in sets.values():
                for code in codes:
                    self.assertTrue(0 < code < 256)


class TestPairCdlYear(unittest.TestCase):
    CDL_YEARS = (2022, 2023, 2024, 2025)

    def test_exact_match_when_available(self):
        for year in self.CDL_YEARS:
            with self.subTest(year=year):
                self.assertEqual(naming.pair_cdl_year(year, self.CDL_YEARS), year)

    def test_years_before_coverage_pair_with_earliest(self):
        for year in range(2015, 2022):
            with self.subTest(year=year):
                self.assertEqual(naming.pair_cdl_year(year, self.CDL_YEARS), 2022)

    def test_years_after_coverage_pair_with_latest(self):
        self.assertEqual(naming.pair_cdl_year(2026, self.CDL_YEARS), 2025)

    def test_raises_when_no_cdl_years(self):
        with self.assertRaises(ValueError):
            naming.pair_cdl_year(2024, ())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_naming -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/__init__.py` as an empty file.

Create `viz/paths.py`:

```python
"""Every filesystem location the project uses, defined once."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CPC_ARCHIVES = ROOT / "usda_crop_progress_and_condition_gridded_layers"
CDL_ARCHIVES = ROOT / "usda_cropland_data_layer"

DATA = ROOT / "data"
CPC_DATA = DATA / "cpc"
CDL_DATA = DATA / "cdl"
MASK_DATA = DATA / "masks"
CATALOG = DATA / "catalog.json"

CACHE = ROOT / "cache"
TILE_CACHE = CACHE / "tiles"

WEB = ROOT / "viz" / "web"
```

Create `viz/naming.py`:

```python
"""CPC filename grammar, CDL crop code sets, and CPC-to-CDL year pairing."""

import re
from typing import Sequence

CROPS = ("corn", "cotton", "soy", "wheat")
VARS = ("cond", "prog")

VAR_LABELS = {"cond": "Condition", "prog": "Progress"}

# CDL class codes per CPC crop. A double-crop class counts toward both of its
# constituent crops, so codes such as 26 and 241 appear under two crops.
# Sweet Corn (12), Pop or Orn Corn (13), and Buckwheat (39) are excluded:
# NASS does not survey them under these progress and condition series.
CROP_CODES = {
    "corn": {"primary": (1,), "double": (225, 226, 228, 237, 241)},
    "cotton": {"primary": (2,), "double": (232, 238, 239)},
    "soy": {"primary": (5,), "double": (26, 239, 240, 241, 254)},
    "wheat": {"primary": (22, 23, 24), "double": (26, 225, 238)},
}

_CPC_RE = re.compile(
    r"^(?P<crop>[a-z]+)(?P<var>Cond|Prog)(?P<yy>\d{2})w(?P<ww>\d{1,2})\.tif$"
)


def parse_cpc_filename(name):
    """Return crop/var/year/week for a CPC raster name, or None if it is not one."""
    match = _CPC_RE.match(name)
    if match is None:
        return None
    crop = match.group("crop")
    if crop not in CROPS:
        return None
    return {
        "crop": crop,
        "var": match.group("var").lower(),
        "year": 2000 + int(match.group("yy")),
        "week": int(match.group("ww")),
    }


def cpc_filename(crop, var, year, week):
    """Build the CPC raster name for one crop, variable, year, and week."""
    return f"{crop}{var.capitalize()}{year % 100:02d}w{week:02d}.tif"


def cpc_relpath(crop, var, year, week):
    """Path of a CPC raster relative to the CPC data directory."""
    return f"{crop}/{var}/{cpc_filename(crop, var, year, week)}"


def pair_cdl_year(cpc_year, cdl_years):
    """Pick the CDL year to show beneath a CPC year, clamping to available coverage."""
    years = sorted(cdl_years)
    if not years:
        raise ValueError("no CDL years available")
    if cpc_year in years:
        return cpc_year
    if cpc_year < years[0]:
        return years[0]
    if cpc_year > years[-1]:
        return years[-1]
    return min(years, key=lambda y: (abs(y - cpc_year), y))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_naming -v`

Expected: PASS, 15 tests

- [ ] **Step 5: Commit**

```bash
git add viz/__init__.py viz/paths.py viz/naming.py tests/__init__.py tests/test_naming.py
git commit -m "Add CPC filename parsing, CDL crop code sets, and year pairing"
```

---

### Task 2: Archive extraction

**Files:**
- Create: `viz/extract.py`, `run.sh`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `viz.paths`, `viz.naming.parse_cpc_filename`
- Produces:
  - `extract.extract_cpc_year(zip_path: Path, dest: Path) -> int` returning the count of rasters written
  - `extract.extract_cdl_year(zip_path: Path, dest: Path) -> list[str]` returning the member names written
  - `extract.main(argv: list[str] | None = None) -> int`
  - On disk: `data/cpc/{crop}/{cond|prog}/*.tif` and `data/cdl/{year}_30m_cdls.tif` plus `.ovr`, `.aux`, `.tfw`

The CPC archives nest one level deeper than they appear: the yearly zip holds `cpc{year}/{crop}/cpc{crop}{year}.zip`, and only that inner archive holds the `condition/` and `progress/` rasters. Crop directory names are discovered from the archive rather than assumed. Only `.tif` members are kept; the `.tfw`, `.ovr`, `.aux.xml`, and Esri `.xml` sidecars are discarded because the GeoTIFF carries its own geotransform and the fixed color ramps make precomputed statistics unnecessary.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract.py`:

```python
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from viz import extract


def _inner_cpc_zip(crop, year, weeks):
    """Build the inner per-crop archive: condition/ and progress/ rasters plus noise."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for week in weeks:
            yy = year % 100
            zf.writestr(f"condition/{crop}Cond{yy:02d}w{week:02d}.tif", b"COND")
            zf.writestr(f"condition/{crop}Cond{yy:02d}w{week:02d}.tif.ovr", b"ovr")
            zf.writestr(f"condition/{crop}Cond{yy:02d}w{week:02d}.tfw", b"tfw")
            zf.writestr(f"progress/{crop}Prog{yy:02d}w{week:02d}.tif", b"PROG")
    return buf.getvalue()


def _outer_cpc_zip(path, year, crops, weeks):
    with zipfile.ZipFile(path, "w") as zf:
        for crop in crops:
            zf.writestr(
                f"cpc{year}/{crop}/cpc{crop}{year}.zip", _inner_cpc_zip(crop, year, weeks)
            )


class TestExtractCpc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.archive = self.tmp / "cpc2024.zip"
        _outer_cpc_zip(self.archive, 2024, ("corn", "soy"), (15, 16))
        self.dest = self.tmp / "cpc"

    def test_writes_only_tif_members_in_flat_layout(self):
        count = extract.extract_cpc_year(self.archive, self.dest)
        self.assertEqual(count, 8)  # 2 crops x 2 vars x 2 weeks
        self.assertTrue((self.dest / "corn/cond/cornCond24w15.tif").is_file())
        self.assertTrue((self.dest / "soy/prog/soyProg24w16.tif").is_file())

    def test_discards_sidecar_files(self):
        extract.extract_cpc_year(self.archive, self.dest)
        written = {p.name for p in self.dest.rglob("*") if p.is_file()}
        self.assertTrue(all(name.endswith(".tif") for name in written))
        self.assertNotIn("cornCond24w15.tfw", written)
        self.assertNotIn("cornCond24w15.tif.ovr", written)

    def test_preserves_raster_content(self):
        extract.extract_cpc_year(self.archive, self.dest)
        self.assertEqual((self.dest / "corn/cond/cornCond24w15.tif").read_bytes(), b"COND")
        self.assertEqual((self.dest / "corn/prog/cornProg24w15.tif").read_bytes(), b"PROG")

    def test_is_idempotent_and_skips_existing(self):
        first = extract.extract_cpc_year(self.archive, self.dest)
        second = extract.extract_cpc_year(self.archive, self.dest)
        self.assertEqual(first, 8)
        self.assertEqual(second, 0)

    def test_rewrites_a_truncated_file(self):
        extract.extract_cpc_year(self.archive, self.dest)
        target = self.dest / "corn/cond/cornCond24w15.tif"
        target.write_bytes(b"XX")
        self.assertEqual(extract.extract_cpc_year(self.archive, self.dest), 1)
        self.assertEqual(target.read_bytes(), b"COND")

    def test_discovers_unexpected_crop_directories(self):
        archive = self.tmp / "cpc2015.zip"
        _outer_cpc_zip(archive, 2015, ("corn",), (20,))
        with zipfile.ZipFile(archive, "a") as zf:
            zf.writestr("cpc2015/notes.txt", b"ignore me")
        self.assertEqual(extract.extract_cpc_year(archive, self.tmp / "cpc15"), 2)


class TestExtractCdl(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.archive = self.tmp / "2024_30m_cdls.zip"
        with zipfile.ZipFile(self.archive, "w") as zf:
            zf.writestr("2024_30m_cdls.tif", b"TIFDATA")
            zf.writestr("2024_30m_cdls.tif.ovr", b"OVRDATA")
            zf.writestr("2024_30m_cdls.aux", b"AUXDATA")
            zf.writestr("2024_30m_cdls.tfw", b"TFWDATA")
            zf.writestr("ReadMe_30meter_CDL.txt", b"readme")
            zf.writestr("metadata_CDL24_FGDC-STD-001-1998.htm", b"<html/>")
        self.dest = self.tmp / "cdl"

    def test_keeps_raster_and_required_sidecars(self):
        written = extract.extract_cdl_year(self.archive, self.dest)
        self.assertEqual(
            sorted(written),
            ["2024_30m_cdls.aux", "2024_30m_cdls.tfw", "2024_30m_cdls.tif", "2024_30m_cdls.tif.ovr"],
        )

    def test_discards_documentation(self):
        extract.extract_cdl_year(self.archive, self.dest)
        names = {p.name for p in self.dest.iterdir()}
        self.assertNotIn("ReadMe_30meter_CDL.txt", names)
        self.assertNotIn("metadata_CDL24_FGDC-STD-001-1998.htm", names)

    def test_is_idempotent(self):
        extract.extract_cdl_year(self.archive, self.dest)
        self.assertEqual(extract.extract_cdl_year(self.archive, self.dest), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_extract -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz.extract'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/extract.py`:

```python
"""One-time, idempotent extraction of the CPC and CDL archives to a flat layout.

The CPC archives nest twice: the yearly zip holds one directory per crop, and
each of those holds a single inner zip whose condition/ and progress/
directories hold the weekly rasters.
"""

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

from viz import naming, paths

CDL_KEEP_SUFFIXES = (".tif", ".tif.ovr", ".aux", ".tfw")
_CDL_YEAR_RE = re.compile(r"^(?P<year>\d{4})_30m_cdls\.zip$")
_CPC_YEAR_RE = re.compile(r"^cpc(?P<year>\d{4})\.zip$")


def _write_if_changed(target, payload):
    """Write payload unless the target already holds exactly that many bytes."""
    if target.exists() and target.stat().st_size == len(payload):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return True


def extract_cpc_year(zip_path, dest):
    """Extract every weekly raster from one yearly CPC archive. Returns files written."""
    written = 0
    with zipfile.ZipFile(zip_path) as outer:
        inner_names = [n for n in outer.namelist() if n.lower().endswith(".zip")]
        for inner_name in inner_names:
            payload = outer.read(inner_name)
            with zipfile.ZipFile(io.BytesIO(payload)) as inner:
                for member in inner.namelist():
                    leaf = member.rsplit("/", 1)[-1]
                    parsed = naming.parse_cpc_filename(leaf)
                    if parsed is None:
                        continue
                    target = dest / naming.cpc_relpath(
                        parsed["crop"], parsed["var"], parsed["year"], parsed["week"]
                    )
                    if _write_if_changed(target, inner.read(member)):
                        written += 1
    return written


def extract_cdl_year(zip_path, dest):
    """Extract the raster and required sidecars from one CDL archive."""
    written = []
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            leaf = member.rsplit("/", 1)[-1]
            if not leaf.endswith(CDL_KEEP_SUFFIXES):
                continue
            if _write_if_changed(dest / leaf, zf.read(member)):
                written.append(leaf)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract the USDA CPC and CDL archives.")
    parser.add_argument("--cpc-only", action="store_true")
    parser.add_argument("--cdl-only", action="store_true")
    args = parser.parse_args(argv)

    if not args.cdl_only:
        for archive in sorted(paths.CPC_ARCHIVES.glob("cpc*.zip")):
            if _CPC_YEAR_RE.match(archive.name) is None:
                continue
            print(f"CPC  {archive.name} ...", flush=True)
            count = extract_cpc_year(archive, paths.CPC_DATA)
            print(f"CPC  {archive.name}: {count} rasters written", flush=True)

    if not args.cpc_only:
        for archive in sorted(paths.CDL_ARCHIVES.glob("*_30m_cdls.zip")):
            if _CDL_YEAR_RE.match(archive.name) is None:
                continue
            print(f"CDL  {archive.name} ... (multi-GB, be patient)", flush=True)
            written = extract_cdl_year(archive, paths.CDL_DATA)
            print(f"CDL  {archive.name}: {len(written)} files written", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `run.sh` and make it executable:

```bash
#!/usr/bin/env bash
# Every entry point runs with PYTHONNOUSERSITE=1 so the system GDAL bindings
# see NumPy 1.21.5 rather than the NumPy 2.x in the user site directory.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONNOUSERSITE=1

case "${1:-serve}" in
  extract) shift; exec python3 -m viz.extract "$@" ;;
  prepare) shift; exec python3 -m viz.prepare "$@" ;;
  serve)   shift; exec python3 -m viz.tileserver "$@" ;;
  test)    shift; exec python3 -m unittest discover -s tests -v "$@" ;;
  *) echo "usage: $0 {extract|prepare|serve|test} [args]" >&2; exit 2 ;;
esac
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && chmod +x run.sh && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_extract -v`

Expected: PASS, 9 tests

- [ ] **Step 5: Run the real CPC extraction**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && ./run.sh extract --cpc-only`

Expected: 12 lines reporting roughly 250 rasters per year. Then verify:

```bash
find data/cpc -name '*.tif' | wc -l      # expect ~3000
ls data/cpc                              # expect: corn cotton soy wheat
du -sh data/cpc                          # expect roughly 1 GB
```

- [ ] **Step 6: Run the real CDL extraction**

This writes about 10 GB and takes several minutes per year. Run it in the background and watch the log:

```bash
cd /mnt/c/Users/emgonz38/Downloads/usda && ./run.sh extract --cdl-only > /tmp/cdl_extract.log 2>&1 &
```

Expected on completion:

```bash
ls -la data/cdl                          # expect 4 years x 4 files
du -sh data/cdl                          # expect roughly 10 GB
```

- [ ] **Step 7: Commit**

```bash
git add viz/extract.py tests/test_extract.py run.sh
git commit -m "Add idempotent extraction for the CPC and CDL archives"
```

---

### Task 3: Web Mercator tile arithmetic

**Files:**
- Create: `viz/gridmath.py`
- Test: `tests/test_gridmath.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `gridmath.WEB_MERCATOR_HALF_SPAN: float` = `20037508.342789244`
  - `gridmath.TILE_SIZE: int` = `256`
  - `gridmath.CPC_GRID: dict` with keys `width`, `height`, `origin_x`, `origin_y`, `pixel_x`, `pixel_y`, `srs`
  - `gridmath.tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]` returning `(minx, miny, maxx, maxy)` in EPSG:3857
  - `gridmath.lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]`
  - `gridmath.cpc_grid_bounds() -> tuple[float, float, float, float]` in EPSG:5070

- [ ] **Step 1: Write the failing test**

Create `tests/test_gridmath.py`:

```python
import math
import unittest

from viz import gridmath

HALF = 20037508.342789244


class TestTileBounds(unittest.TestCase):
    def test_zoom_zero_covers_the_whole_world(self):
        minx, miny, maxx, maxy = gridmath.tile_bounds(0, 0, 0)
        self.assertAlmostEqual(minx, -HALF, places=6)
        self.assertAlmostEqual(miny, -HALF, places=6)
        self.assertAlmostEqual(maxx, HALF, places=6)
        self.assertAlmostEqual(maxy, HALF, places=6)

    def test_zoom_one_quadrants_meet_at_the_origin(self):
        top_left = gridmath.tile_bounds(1, 0, 0)
        bottom_right = gridmath.tile_bounds(1, 1, 1)
        self.assertAlmostEqual(top_left[2], 0.0, places=6)   # maxx
        self.assertAlmostEqual(top_left[1], 0.0, places=6)   # miny
        self.assertAlmostEqual(bottom_right[0], 0.0, places=6)
        self.assertAlmostEqual(bottom_right[3], 0.0, places=6)

    def test_y_increases_downward(self):
        upper = gridmath.tile_bounds(3, 4, 2)
        lower = gridmath.tile_bounds(3, 4, 3)
        self.assertGreater(upper[1], lower[1])

    def test_tiles_are_square_at_every_zoom(self):
        for z in range(0, 15):
            minx, miny, maxx, maxy = gridmath.tile_bounds(z, 0, 0)
            with self.subTest(z=z):
                self.assertAlmostEqual(maxx - minx, maxy - miny, places=6)

    def test_adjacent_tiles_share_an_edge_without_gap_or_overlap(self):
        left = gridmath.tile_bounds(6, 10, 20)
        right = gridmath.tile_bounds(6, 11, 20)
        self.assertAlmostEqual(left[2], right[0], places=9)


class TestLonLatToTile(unittest.TestCase):
    def test_null_island_at_zoom_one(self):
        self.assertEqual(gridmath.lonlat_to_tile(0.0001, 0.0001, 1), (1, 0))

    def test_ames_iowa_matches_slippy_map_reference(self):
        # Reference values from the standard slippy map formula.
        self.assertEqual(gridmath.lonlat_to_tile(-93.62, 42.03, 7), (30, 47))
        self.assertEqual(gridmath.lonlat_to_tile(-93.62, 42.03, 10), (245, 380))

    def test_round_trips_into_the_tile_it_names(self):
        lon, lat, z = -93.62, 42.03, 9
        x, y = gridmath.lonlat_to_tile(lon, lat, z)
        minx, miny, maxx, maxy = gridmath.tile_bounds(z, x, y)
        mx = HALF * lon / 180.0
        my = HALF * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi
        self.assertTrue(minx <= mx <= maxx)
        self.assertTrue(miny <= my <= maxy)


class TestCpcGrid(unittest.TestCase):
    def test_matches_the_canonical_grid_in_the_spec(self):
        self.assertEqual(gridmath.CPC_GRID["width"], 508)
        self.assertEqual(gridmath.CPC_GRID["height"], 320)
        self.assertAlmostEqual(gridmath.CPC_GRID["origin_x"], -2309800.213402342982590, places=6)
        self.assertAlmostEqual(gridmath.CPC_GRID["origin_y"], 3185470.286793155129999, places=6)
        self.assertAlmostEqual(gridmath.CPC_GRID["pixel_x"], 8999.255456289853100, places=6)
        self.assertAlmostEqual(gridmath.CPC_GRID["pixel_y"], -8995.486488541766448, places=6)
        self.assertEqual(gridmath.CPC_GRID["srs"], "EPSG:5070")

    def test_bounds_are_ordered_and_span_the_grid(self):
        minx, miny, maxx, maxy = gridmath.cpc_grid_bounds()
        self.assertLess(minx, maxx)
        self.assertLess(miny, maxy)
        self.assertAlmostEqual(maxx - minx, 508 * 8999.255456289853100, places=3)
        self.assertAlmostEqual(maxy - miny, 320 * 8995.486488541766448, places=3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_gridmath -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz.gridmath'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/gridmath.py`:

```python
"""Web Mercator tile arithmetic and the canonical CPC grid definition."""

import math

WEB_MERCATOR_HALF_SPAN = 20037508.342789244
TILE_SIZE = 256

# The maximum CPC extent observed across the archive. Masks are written on this
# grid; CPC rasters keep their own per-week extents and are sampled by
# coordinate, so the two never need to nest in pixel space.
CPC_GRID = {
    "width": 508,
    "height": 320,
    "origin_x": -2309800.213402342982590,
    "origin_y": 3185470.286793155129999,
    "pixel_x": 8999.255456289853100,
    "pixel_y": -8995.486488541766448,
    "srs": "EPSG:5070",
}


def tile_bounds(z, x, y):
    """EPSG:3857 (minx, miny, maxx, maxy) of one XYZ tile, y increasing southward."""
    span = 2.0 * WEB_MERCATOR_HALF_SPAN / (2 ** z)
    minx = -WEB_MERCATOR_HALF_SPAN + x * span
    maxy = WEB_MERCATOR_HALF_SPAN - y * span
    return (minx, maxy - span, minx + span, maxy)


def lonlat_to_tile(lon, lat, z):
    """XYZ tile containing a longitude and latitude at one zoom level."""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return (max(0, min(n - 1, x)), max(0, min(n - 1, y)))


def cpc_grid_bounds():
    """EPSG:5070 (minx, miny, maxx, maxy) of the canonical CPC grid."""
    minx = CPC_GRID["origin_x"]
    maxy = CPC_GRID["origin_y"]
    maxx = minx + CPC_GRID["width"] * CPC_GRID["pixel_x"]
    miny = maxy + CPC_GRID["height"] * CPC_GRID["pixel_y"]
    return (minx, miny, maxx, maxy)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_gridmath -v`

Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add viz/gridmath.py tests/test_gridmath.py
git commit -m "Add Web Mercator tile arithmetic and canonical CPC grid"
```

---

### Task 4: Color lookup tables

**Files:**
- Create: `viz/color.py`
- Test: `tests/test_color.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `color.COND_DOMAIN: tuple[float, float]` = `(1.0, 5.0)`
  - `color.PROG_DOMAIN: tuple[float, float]` = `(0.0, 1.0)`
  - `color.RAMPS: dict[str, list[tuple[float, tuple[int, int, int]]]]` keyed `"cond"` and `"prog"`
  - `color.ramp_lut(var: str) -> np.ndarray` shape `(256, 4)` dtype `uint8`
  - `color.palette_lut(color_table) -> np.ndarray` shape `(256, 4)` dtype `uint8`, taking a GDAL `ColorTable` or `None`
  - `color.colorize_continuous(values, var, alpha=None) -> np.ndarray` shape `(H, W, 4)` dtype `uint8`
  - `color.colorize_thematic(codes, lut) -> np.ndarray` shape `(H, W, 4)` dtype `uint8`
  - `color.legend_stops(var: str) -> list[dict]` for the interface legend

The condition ramp is diverging red through neutral at 3.0 (fair) to green, pinned to the NASS scale. The progress ramp is sequential. Both domains are fixed so a color means the same thing in every frame.

- [ ] **Step 1: Write the failing test**

Create `tests/test_color.py`:

```python
import unittest

import numpy as np

from viz import color


class TestRampLut(unittest.TestCase):
    def test_shape_and_dtype(self):
        for var in ("cond", "prog"):
            with self.subTest(var=var):
                lut = color.ramp_lut(var)
                self.assertEqual(lut.shape, (256, 4))
                self.assertEqual(lut.dtype, np.uint8)

    def test_fully_opaque_across_the_ramp(self):
        for var in ("cond", "prog"):
            with self.subTest(var=var):
                self.assertTrue((color.ramp_lut(var)[:, 3] == 255).all())

    def test_condition_ramp_runs_red_to_green(self):
        lut = color.ramp_lut("cond")
        low, high = lut[0], lut[255]
        self.assertGreater(int(low[0]), int(low[1]))    # low end is red-dominant
        self.assertGreater(int(high[1]), int(high[0]))  # high end is green-dominant

    def test_ramp_is_monotonic_in_perceived_lightness_free_hue(self):
        # Adjacent entries must not jump: a ramp with a discontinuity reads as banding.
        for var in ("cond", "prog"):
            lut = color.ramp_lut(var).astype(int)
            deltas = np.abs(np.diff(lut[:, :3], axis=0)).max()
            with self.subTest(var=var):
                self.assertLessEqual(deltas, 12)

    def test_unknown_variable_raises(self):
        with self.assertRaises(KeyError):
            color.ramp_lut("nope")


class TestColorizeContinuous(unittest.TestCase):
    def test_domain_endpoints_hit_ramp_endpoints(self):
        values = np.array([[1.0, 5.0]], dtype=np.float32)
        out = color.colorize_continuous(values, "cond")
        lut = color.ramp_lut("cond")
        np.testing.assert_array_equal(out[0, 0, :3], lut[0, :3])
        np.testing.assert_array_equal(out[0, 1, :3], lut[255, :3])

    def test_values_outside_the_domain_clamp(self):
        values = np.array([[0.2, 9.9]], dtype=np.float32)
        out = color.colorize_continuous(values, "cond")
        lut = color.ramp_lut("cond")
        np.testing.assert_array_equal(out[0, 0, :3], lut[0, :3])
        np.testing.assert_array_equal(out[0, 1, :3], lut[255, :3])

    def test_nan_and_nodata_are_transparent(self):
        values = np.array([[np.nan, -9999.0, 3.0]], dtype=np.float32)
        out = color.colorize_continuous(values, "cond")
        self.assertEqual(int(out[0, 0, 3]), 0)
        self.assertEqual(int(out[0, 1, 3]), 0)
        self.assertEqual(int(out[0, 2, 3]), 255)

    def test_alpha_argument_scales_opacity_and_still_zeroes_nodata(self):
        values = np.array([[3.0, 3.0, np.nan]], dtype=np.float32)
        alpha = np.array([[0.0, 0.5, 1.0]], dtype=np.float32)
        out = color.colorize_continuous(values, "cond", alpha=alpha)
        self.assertEqual(int(out[0, 0, 3]), 0)
        self.assertEqual(int(out[0, 1, 3]), 127)
        self.assertEqual(int(out[0, 2, 3]), 0)

    def test_progress_domain_is_zero_to_one(self):
        values = np.array([[0.0, 1.0]], dtype=np.float32)
        out = color.colorize_continuous(values, "prog")
        lut = color.ramp_lut("prog")
        np.testing.assert_array_equal(out[0, 0, :3], lut[0, :3])
        np.testing.assert_array_equal(out[0, 1, :3], lut[255, :3])

    def test_output_shape_matches_input(self):
        values = np.full((7, 11), 3.0, dtype=np.float32)
        self.assertEqual(color.colorize_continuous(values, "cond").shape, (7, 11, 4))


class _FakeColorTable:
    def __init__(self, entries):
        self._entries = entries

    def GetColorEntry(self, index):
        return self._entries.get(index)


class TestPaletteLut(unittest.TestCase):
    def test_background_class_zero_is_transparent(self):
        table = _FakeColorTable({0: (0, 0, 0, 255), 1: (255, 210, 0, 255)})
        lut = color.palette_lut(table)
        self.assertEqual(tuple(lut[0]), (0, 0, 0, 0))

    def test_known_classes_keep_their_colors(self):
        table = _FakeColorTable({1: (255, 210, 0, 255), 5: (36, 110, 0, 255)})
        lut = color.palette_lut(table)
        self.assertEqual(tuple(lut[1]), (255, 210, 0, 255))
        self.assertEqual(tuple(lut[5]), (36, 110, 0, 255))

    def test_missing_entries_are_transparent(self):
        lut = color.palette_lut(_FakeColorTable({1: (255, 210, 0, 255)}))
        self.assertEqual(tuple(lut[200]), (0, 0, 0, 0))

    def test_none_color_table_yields_a_fully_transparent_lut(self):
        lut = color.palette_lut(None)
        self.assertEqual(lut.shape, (256, 4))
        self.assertTrue((lut[:, 3] == 0).all())


class TestColorizeThematic(unittest.TestCase):
    def test_maps_codes_through_the_lut(self):
        lut = color.palette_lut(_FakeColorTable({1: (255, 210, 0, 255)}))
        codes = np.array([[1, 0]], dtype=np.uint8)
        out = color.colorize_thematic(codes, lut)
        self.assertEqual(tuple(out[0, 0]), (255, 210, 0, 255))
        self.assertEqual(int(out[0, 1, 3]), 0)


class TestLegendStops(unittest.TestCase):
    def test_condition_stops_name_the_nass_categories(self):
        labels = [stop["label"] for stop in color.legend_stops("cond")]
        self.assertEqual(labels, ["Very poor", "Poor", "Fair", "Good", "Excellent"])

    def test_every_stop_carries_a_value_and_a_css_color(self):
        for var in ("cond", "prog"):
            for stop in color.legend_stops(var):
                with self.subTest(var=var, stop=stop):
                    self.assertIn("value", stop)
                    self.assertTrue(stop["color"].startswith("#"))
                    self.assertEqual(len(stop["color"]), 7)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_color -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz.color'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/color.py`:

```python
"""Lookup tables mapping raster values to RGBA.

Both CPC domains are fixed rather than stretched per frame, so a color carries
the same meaning in every week, crop, and year and animation stays comparable.
"""

import numpy as np

NODATA = -9999.0

COND_DOMAIN = (1.0, 5.0)
PROG_DOMAIN = (0.0, 1.0)

DOMAINS = {"cond": COND_DOMAIN, "prog": PROG_DOMAIN}

# Control points as (position in 0-1, (r, g, b)).
RAMPS = {
    # Diverging: very poor through fair at the midpoint to excellent.
    "cond": [
        (0.00, (165, 15, 21)),
        (0.25, (222, 92, 55)),
        (0.50, (247, 224, 143)),
        (0.75, (120, 183, 92)),
        (1.00, (26, 110, 47)),
    ],
    # Sequential: unplanted through fully progressed.
    "prog": [
        (0.00, (247, 244, 233)),
        (0.33, (196, 214, 172)),
        (0.66, (110, 166, 148)),
        (1.00, (25, 74, 110)),
    ],
}

COND_LABELS = ["Very poor", "Poor", "Fair", "Good", "Excellent"]


def ramp_lut(var):
    """Build a 256-entry RGBA lookup table for one continuous variable."""
    stops = RAMPS[var]
    positions = np.array([p for p, _ in stops], dtype=np.float64)
    colors = np.array([c for _, c in stops], dtype=np.float64)
    t = np.linspace(0.0, 1.0, 256)
    lut = np.empty((256, 4), dtype=np.uint8)
    for channel in range(3):
        lut[:, channel] = np.interp(t, positions, colors[:, channel]).round().astype(np.uint8)
    lut[:, 3] = 255
    return lut


def palette_lut(color_table):
    """Build a 256-entry RGBA lookup table from a GDAL color table.

    Class 0 is Background and always renders transparent, as do any codes the
    table does not define.
    """
    lut = np.zeros((256, 4), dtype=np.uint8)
    if color_table is None:
        return lut
    for index in range(256):
        entry = color_table.GetColorEntry(index)
        if entry is None:
            continue
        rgba = tuple(entry) + (255,) * (4 - len(entry))
        lut[index] = rgba[:4]
    lut[0] = (0, 0, 0, 0)
    return lut


def colorize_continuous(values, var, alpha=None):
    """Map a float array to RGBA through the fixed ramp for that variable.

    NaN and the CPC NoData sentinel render fully transparent. When alpha is
    given (a 0-1 array of the same shape, typically a crop fraction) it scales
    opacity multiplicatively.
    """
    lo, hi = DOMAINS[var]
    data = np.asarray(values, dtype=np.float32)
    invalid = ~np.isfinite(data) | (data == NODATA)

    scaled = (np.nan_to_num(data, nan=lo, posinf=hi, neginf=lo) - lo) / (hi - lo)
    indices = np.clip(scaled * 255.0, 0.0, 255.0).astype(np.uint8)

    out = ramp_lut(var)[indices]
    if alpha is not None:
        weight = np.clip(np.asarray(alpha, dtype=np.float32), 0.0, 1.0)
        out[..., 3] = (out[..., 3] * weight).round().astype(np.uint8)
    out[invalid, 3] = 0
    return out


def colorize_thematic(codes, lut):
    """Map a byte class array to RGBA through a palette lookup table."""
    return lut[np.asarray(codes, dtype=np.uint8)]


def legend_stops(var):
    """Legend entries for one variable: value, label, and CSS color."""
    lo, hi = DOMAINS[var]
    lut = ramp_lut(var)

    def css(value):
        index = int(round(np.clip((value - lo) / (hi - lo), 0.0, 1.0) * 255))
        r, g, b = lut[index, :3]
        return f"#{r:02x}{g:02x}{b:02x}"

    if var == "cond":
        return [
            {"value": value, "label": label, "color": css(value)}
            for value, label in zip((1.0, 2.0, 3.0, 4.0, 5.0), COND_LABELS)
        ]
    return [
        {"value": value, "label": f"{int(value * 100)}%", "color": css(value)}
        for value in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_color -v`

Expected: PASS, 18 tests

- [ ] **Step 5: Commit**

```bash
git add viz/color.py tests/test_color.py
git commit -m "Add fixed-domain color ramps and CDL palette lookup tables"
```

---

### Task 5: Raster access and tile warping

**Files:**
- Create: `viz/rasters.py`, `tests/fixtures.py`
- Test: `tests/test_rasters.py`

**Interfaces:**
- Consumes: `viz.gridmath.tile_bounds`, `viz.gridmath.CPC_GRID`, `viz.color`
- Produces:
  - `rasters.open_cached(path: str) -> gdal.Dataset` — thread-local, opens once per thread
  - `rasters.warp_tile(path, z, x, y, resample="near", bands=None, dtype=None) -> np.ndarray` shape `(H, W)` for one band or `(B, H, W)` for several
  - `rasters.sample_point(path, x5070, y5070, bands=None) -> list[float]`
  - `rasters.lonlat_to_5070(lon: float, lat: float) -> tuple[float, float]`
  - `rasters.encode_png(rgba: np.ndarray) -> bytes`
  - `rasters.read_rat(path: str) -> dict[int, str]` mapping CDL code to class name

GDAL `Dataset` objects are not safe for concurrent access, so the cache lives in `threading.local()` and each worker thread opens its own handle. Warping uses `overviewLevel="AUTO"` so GDAL reads the appropriate `.ovr` pyramid level, which is what keeps a 153811 × 96523 raster responsive at national zoom.

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures.py`:

```python
"""Small synthetic rasters on the real CRSs, so tests need no multi-GB inputs."""

import numpy as np
from osgeo import gdal, osr

from viz import gridmath

gdal.UseExceptions()


def _srs_wkt(epsg):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    return srs.ExportToWkt()


def write_cpc_like(path, width=40, height=30, fill=3.0, nodata=-9999.0, border_nodata=True):
    """A Float32 EPSG:5070 raster on the canonical CPC grid's origin and pixel size."""
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(str(path), width, height, 1, gdal.GDT_Float32)
    ds.SetGeoTransform(
        (
            gridmath.CPC_GRID["origin_x"],
            gridmath.CPC_GRID["pixel_x"],
            0.0,
            gridmath.CPC_GRID["origin_y"],
            0.0,
            gridmath.CPC_GRID["pixel_y"],
        )
    )
    ds.SetProjection(_srs_wkt(5070))
    data = np.full((height, width), fill, dtype=np.float32)
    if border_nodata:
        data[0, :] = nodata
        data[-1, :] = nodata
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(data)
    ds.FlushCache()
    return path


def write_cdl_like(path, width=40, height=30, code=1, pixel=30.0):
    """A paletted Byte EPSG:5070 raster carrying a color table and an attribute table."""
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(str(path), width, height, 1, gdal.GDT_Byte)
    ds.SetGeoTransform(
        (gridmath.CPC_GRID["origin_x"], pixel, 0.0, gridmath.CPC_GRID["origin_y"], 0.0, -pixel)
    )
    ds.SetProjection(_srs_wkt(5070))

    data = np.zeros((height, width), dtype=np.uint8)
    data[:, width // 2:] = code  # left half Background, right half the crop
    band = ds.GetRasterBand(1)

    table = gdal.ColorTable()
    table.SetColorEntry(0, (0, 0, 0, 255))
    table.SetColorEntry(1, (255, 210, 0, 255))
    table.SetColorEntry(5, (36, 110, 0, 255))
    band.SetRasterColorTable(table)

    rat = gdal.RasterAttributeTable()
    rat.CreateColumn("Count", gdal.GFT_Integer, gdal.GFU_PixelCount)
    rat.CreateColumn("Class_Name", gdal.GFT_String, gdal.GFU_Name)
    for row, (value, name) in enumerate(((0, "Background"), (1, "Corn"), (5, "Soybeans"))):
        rat.SetValueAsInt(row, 0, int((data == value).sum()))
        rat.SetValueAsString(row, 1, name)
    band.SetDefaultRAT(rat)

    band.WriteArray(data)
    ds.FlushCache()
    return path


def tile_covering(path):
    """A (z, x, y) tile whose extent overlaps the fixture, for warp tests."""
    return (7, 30, 47)
```

Create `tests/test_rasters.py`:

```python
import shutil
import struct
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from tests import fixtures
from viz import gridmath, rasters


class TestOpenCached(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = str(fixtures.write_cdl_like(self.tmp / "cdl.tif"))

    def test_returns_the_same_handle_within_one_thread(self):
        self.assertIs(rasters.open_cached(self.path), rasters.open_cached(self.path))

    def test_each_thread_gets_its_own_handle(self):
        main = rasters.open_cached(self.path)
        other = {}

        def worker():
            other["ds"] = rasters.open_cached(self.path)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertIsNot(main, other["ds"])

    def test_missing_file_raises(self):
        with self.assertRaises(Exception):
            rasters.open_cached(str(self.tmp / "absent.tif"))


class TestWarpTile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cdl = str(fixtures.write_cdl_like(self.tmp / "cdl.tif"))
        self.cpc = str(fixtures.write_cpc_like(self.tmp / "cpc.tif"))

    def test_returns_a_256_square_array(self):
        z, x, y = fixtures.tile_covering(self.cdl)
        out = rasters.warp_tile(self.cdl, z, x, y, resample="near")
        self.assertEqual(out.shape, (gridmath.TILE_SIZE, gridmath.TILE_SIZE))

    def test_thematic_warp_preserves_exact_class_codes(self):
        z, x, y = fixtures.tile_covering(self.cdl)
        out = rasters.warp_tile(self.cdl, z, x, y, resample="near")
        self.assertTrue(set(np.unique(out)).issubset({0, 1}))

    def test_continuous_warp_returns_float(self):
        z, x, y = fixtures.tile_covering(self.cpc)
        out = rasters.warp_tile(self.cpc, z, x, y, resample="bilinear", dtype="float32")
        self.assertEqual(out.dtype, np.float32)

    def test_multiband_request_returns_band_major_array(self):
        path = str(self.tmp / "two.tif")
        rasters.write_multiband_float(path, np.zeros((2, 30, 40), dtype=np.float32),
                                      gridmath.CPC_GRID)
        z, x, y = fixtures.tile_covering(path)
        out = rasters.warp_tile(path, z, x, y, resample="bilinear", bands=[1, 2],
                                dtype="float32")
        self.assertEqual(out.shape, (2, gridmath.TILE_SIZE, gridmath.TILE_SIZE))

    def test_tile_far_from_the_data_is_all_nodata(self):
        out = rasters.warp_tile(self.cdl, 7, 5, 5, resample="near")
        self.assertTrue((out == 0).all())


class TestSamplePoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cpc = str(fixtures.write_cpc_like(self.tmp / "cpc.tif", fill=3.75))

    def test_reads_the_value_at_a_projected_coordinate(self):
        x = gridmath.CPC_GRID["origin_x"] + 5 * gridmath.CPC_GRID["pixel_x"]
        y = gridmath.CPC_GRID["origin_y"] + 5 * gridmath.CPC_GRID["pixel_y"]
        self.assertAlmostEqual(rasters.sample_point(self.cpc, x, y)[0], 3.75, places=4)

    def test_point_outside_the_raster_returns_none(self):
        self.assertIsNone(rasters.sample_point(self.cpc, 0.0, 0.0)[0])


class TestLonLatTo5070(unittest.TestCase):
    def test_ames_iowa_lands_in_the_corn_belt(self):
        x, y = rasters.lonlat_to_5070(-93.62, 42.03)
        self.assertTrue(-500000 < x < 500000)
        self.assertTrue(1800000 < y < 2400000)

    def test_result_is_finite(self):
        x, y = rasters.lonlat_to_5070(-96.0, 40.0)
        self.assertTrue(np.isfinite(x) and np.isfinite(y))


class TestEncodePng(unittest.TestCase):
    def test_emits_a_png_signature(self):
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        self.assertTrue(rasters.encode_png(rgba).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_ihdr_declares_the_expected_dimensions(self):
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        blob = rasters.encode_png(rgba)
        width, height = struct.unpack(">II", blob[16:24])
        self.assertEqual((width, height), (256, 256))

    def test_alpha_survives_the_round_trip(self):
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        rgba[..., 3] = 128
        self.assertGreater(len(rasters.encode_png(rgba)), 100)


class TestReadRat(unittest.TestCase):
    def test_maps_codes_to_class_names(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = str(fixtures.write_cdl_like(tmp / "cdl.tif"))
        classes = rasters.read_rat(path)
        self.assertEqual(classes[0], "Background")
        self.assertEqual(classes[1], "Corn")
        self.assertEqual(classes[5], "Soybeans")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_rasters -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz.rasters'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/rasters.py`:

```python
"""GDAL access: thread-local dataset caching, tile warping, and point sampling."""

import threading

import numpy as np
from osgeo import gdal, osr

from viz import gridmath

gdal.UseExceptions()
gdal.SetCacheMax(512 * 1024 * 1024)

_local = threading.local()

RESAMPLE = {
    "near": gdal.GRA_NearestNeighbour,
    "bilinear": gdal.GRA_Bilinear,
    "average": gdal.GRA_Average,
}

DTYPES = {"byte": gdal.GDT_Byte, "float32": gdal.GDT_Float32}


def open_cached(path):
    """Open a raster, reusing one handle per thread.

    GDAL Dataset objects are not safe for concurrent access, so the cache is
    thread-local rather than shared.
    """
    cache = getattr(_local, "datasets", None)
    if cache is None:
        cache = _local.datasets = {}
    ds = cache.get(path)
    if ds is None:
        ds = cache[path] = gdal.Open(path, gdal.GA_ReadOnly)
    return ds


def warp_tile(path, z, x, y, resample="near", bands=None, dtype=None):
    """Warp one XYZ tile out of a raster into a 256x256 Web Mercator array."""
    src = open_cached(path)
    options = {
        "format": "MEM",
        "dstSRS": "EPSG:3857",
        "outputBounds": gridmath.tile_bounds(z, x, y),
        "width": gridmath.TILE_SIZE,
        "height": gridmath.TILE_SIZE,
        "resampleAlg": RESAMPLE[resample],
        "overviewLevel": "AUTO",
        "multithread": True,
    }
    if bands is not None:
        options["srcBands"] = list(bands)
        options["dstBands"] = list(range(1, len(bands) + 1))
    if dtype is not None:
        options["outputType"] = DTYPES[dtype]

    out = gdal.Warp("", src, **options)
    array = out.ReadAsArray()
    if bands is not None and len(bands) > 1:
        return array
    return array if array.ndim == 2 else array[0]


def write_multiband_float(path, data, grid):
    """Write a (bands, rows, cols) float array onto a grid definition. Used by masks."""
    bands, height, width = data.shape
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        str(path), width, height, bands, gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES"],
    )
    ds.SetGeoTransform(
        (grid["origin_x"], grid["pixel_x"], 0.0, grid["origin_y"], 0.0, grid["pixel_y"])
    )
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(grid["srs"].split(":")[1]))
    ds.SetProjection(srs.ExportToWkt())
    for index in range(bands):
        ds.GetRasterBand(index + 1).WriteArray(data[index])
    ds.FlushCache()
    return path


def sample_point(path, x5070, y5070, bands=None):
    """Read one pixel by projected coordinate. Returns None per band when outside."""
    ds = open_cached(path)
    gt = ds.GetGeoTransform()
    col = int((x5070 - gt[0]) / gt[1])
    row = int((y5070 - gt[3]) / gt[5])
    indices = list(bands) if bands is not None else [1]
    if not (0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize):
        return [None] * len(indices)

    values = []
    for index in indices:
        band = ds.GetRasterBand(index)
        value = float(band.ReadAsArray(col, row, 1, 1)[0][0])
        nodata = band.GetNoDataValue()
        if not np.isfinite(value) or (nodata is not None and value == nodata):
            values.append(None)
        else:
            values.append(value)
    return values


_transform = None


def lonlat_to_5070(lon, lat):
    """Transform WGS84 longitude and latitude to NAD83 / Conus Albers."""
    global _transform
    if _transform is None:
        source = osr.SpatialReference()
        source.ImportFromEPSG(4326)
        source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        target = osr.SpatialReference()
        target.ImportFromEPSG(5070)
        target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        _transform = osr.CoordinateTransformation(source, target)
    x, y, _ = _transform.TransformPoint(float(lon), float(lat))
    return (x, y)


def encode_png(rgba):
    """Encode an (H, W, 4) uint8 array as PNG bytes through GDAL's PNG driver."""
    height, width = rgba.shape[:2]
    mem = gdal.GetDriverByName("MEM").Create("", width, height, 4, gdal.GDT_Byte)
    for index in range(4):
        mem.GetRasterBand(index + 1).WriteArray(rgba[..., index])

    name = f"/vsimem/tile_{threading.get_ident()}_{id(rgba)}.png"
    gdal.GetDriverByName("PNG").CreateCopy(name, mem)
    handle = gdal.VSIFOpenL(name, "rb")
    gdal.VSIFSeekL(handle, 0, 2)
    size = gdal.VSIFTellL(handle)
    gdal.VSIFSeekL(handle, 0, 0)
    blob = gdal.VSIFReadL(1, size, handle)
    gdal.VSIFCloseL(handle)
    gdal.Unlink(name)
    return blob


def read_rat(path):
    """Read a CDL raster attribute table into a code-to-class-name map."""
    band = open_cached(path).GetRasterBand(1)
    rat = band.GetDefaultRAT()
    if rat is None:
        return {}

    name_col = None
    for index in range(rat.GetColumnCount()):
        if rat.GetUsageOfCol(index) == gdal.GFU_Name or rat.GetNameOfCol(index) == "Class_Name":
            name_col = index
            break
    if name_col is None:
        return {}

    classes = {}
    for row in range(rat.GetRowCount()):
        label = rat.GetValueAsString(row, name_col).strip()
        if label:
            classes[row] = label
    return classes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_rasters -v`

Expected: PASS, 16 tests

- [ ] **Step 5: Verify against the real extracted CDL**

This confirms the palette and overview behavior on real data rather than a fixture:

```bash
cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -c "
from viz import rasters, color, gridmath
import numpy as np, time
path = 'data/cdl/2024_30m_cdls.tif'
classes = rasters.read_rat(path)
print('classes:', classes[1], classes[5], classes[24])
lut = color.palette_lut(rasters.open_cached(path).GetRasterBand(1).GetRasterColorTable())
print('corn color:', tuple(lut[1]))
for z in (5, 8, 11, 14):
    x, y = gridmath.lonlat_to_tile(-93.62, 42.03, z)
    t0 = time.time()
    tile = rasters.warp_tile(path, z, x, y, resample='near')
    rgba = color.colorize_thematic(tile, lut)
    print(f'z={z:2d} {(time.time()-t0)*1000:7.1f} ms  png={len(rasters.encode_png(rgba)):6d} B')
"
```

Expected: class names `Corn`, `Soybeans`, `Winter Wheat`; corn color `(255, 210, 0, 255)`; every zoom under roughly 1 second, with the coarse zooms fastest once the overviews are read from the extracted file rather than the zip.

- [ ] **Step 6: Commit**

```bash
git add viz/rasters.py tests/fixtures.py tests/test_rasters.py
git commit -m "Add thread-local raster access, tile warping, and PNG encoding"
```

---

### Task 6: Catalog and crop-fraction masks

**Files:**
- Create: `viz/prepare.py`
- Test: `tests/test_prepare.py`

**Interfaces:**
- Consumes: `viz.naming`, `viz.paths`, `viz.gridmath.CPC_GRID`, `viz.rasters.write_multiband_float`, `viz.rasters.read_rat`
- Produces:
  - `prepare.scan_cpc(cpc_root: Path) -> dict` nested `crop -> var -> str(year) -> sorted list of weeks`
  - `prepare.scan_cdl(cdl_root: Path) -> list[int]`
  - `prepare.build_lut_vrt(src_path: str, code_sets: Sequence[Sequence[int]]) -> str` returning VRT XML
  - `prepare.build_mask(src_path: str, crop: str, out_path: Path) -> Path`
  - `prepare.scan_masks(mask_root: Path) -> list[int]`
  - `prepare.build_catalog(cpc_root, cdl_root, mask_root) -> dict`
  - `prepare.main(argv=None) -> int`
  - On disk: `data/catalog.json`, `data/masks/{cdl_year}_{crop}_frac9km.tif`

The catalog's `mask_years` key lists the CDL years that have a complete set of four masks on disk. The interface reads it to enable or disable the mask control, so building masks for more years later needs no code change.

Masks are built one CDL year at a time. A single VRT wraps the CDL with eight bands over the same source band, each carrying a `<LUT>` collapsing the 256 class codes to 0 or 1 for one crop's primary or double-crop set, with `ColorInterp` forced to Gray so the palette is not applied. One `gdal.Warp` resamples all eight to the 9 km grid with `resampleAlg="average"`, which turns a 0/1 indicator into an exact areal fraction.

`overviewLevel="NONE"` is required. The `.ovr` pyramid was built for a thematic raster, so averaging a class indicator sampled from an overview would not give a true areal fraction. This forces a full-resolution pass per year, which is the plan's one slow step.

A measured probe warped one sixty-fourth of the 2024 raster across all eight bands in 134.8 s reading through `/vsizip`, projecting to roughly 144 minutes per year. Reading the extracted file instead of decompressing the zip on every access will cut that substantially, by an amount nobody has measured yet. Step 6 below therefore times one year before the remaining three are started.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prepare.py`:

```python
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal

from tests import fixtures
from viz import naming, prepare


class TestScanCpc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for rel in (
            "corn/cond/cornCond24w15.tif",
            "corn/cond/cornCond24w16.tif",
            "corn/cond/cornCond23w20.tif",
            "corn/prog/cornProg24w15.tif",
            "soy/cond/soyCond24w22.tif",
            "corn/cond/notes.txt",
        ):
            target = self.tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")

    def test_groups_weeks_by_crop_variable_and_year(self):
        found = prepare.scan_cpc(self.tmp)
        self.assertEqual(found["corn"]["cond"]["2024"], [15, 16])
        self.assertEqual(found["corn"]["cond"]["2023"], [20])
        self.assertEqual(found["corn"]["prog"]["2024"], [15])
        self.assertEqual(found["soy"]["cond"]["2024"], [22])

    def test_ignores_non_raster_files(self):
        found = prepare.scan_cpc(self.tmp)
        self.assertNotIn("notes", str(found))

    def test_weeks_are_sorted_ascending(self):
        (self.tmp / "corn/cond/cornCond24w09.tif").write_bytes(b"x")
        self.assertEqual(prepare.scan_cpc(self.tmp)["corn"]["cond"]["2024"], [9, 15, 16])


class TestScanCdl(unittest.TestCase):
    def test_reports_years_present_sorted(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name in ("2024_30m_cdls.tif", "2022_30m_cdls.tif", "2022_30m_cdls.tif.ovr"):
            (tmp / name).write_bytes(b"x")
        self.assertEqual(prepare.scan_cdl(tmp), [2022, 2024])


class TestBuildLutVrt(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = str(fixtures.write_cdl_like(self.tmp / "cdl.tif", code=1))

    def test_produces_one_band_per_code_set(self):
        xml = prepare.build_lut_vrt(self.src, [(1,), (5,), (22, 23, 24)])
        ds = gdal.Open(xml)
        self.assertEqual(ds.RasterCount, 3)

    def test_bands_are_gray_not_paletted(self):
        ds = gdal.Open(prepare.build_lut_vrt(self.src, [(1,)]))
        interp = ds.GetRasterBand(1).GetColorInterpretation()
        self.assertEqual(gdal.GetColorInterpretationName(interp), "Gray")

    def test_lut_maps_member_codes_to_one_and_others_to_zero(self):
        ds = gdal.Open(prepare.build_lut_vrt(self.src, [(1,)]))
        raw = gdal.Open(self.src).GetRasterBand(1).ReadAsArray()
        lutted = ds.GetRasterBand(1).ReadAsArray()
        np.testing.assert_array_equal(lutted, (raw == 1).astype(np.uint8))

    def test_empty_code_set_yields_an_all_zero_band(self):
        ds = gdal.Open(prepare.build_lut_vrt(self.src, [()]))
        self.assertTrue((ds.GetRasterBand(1).ReadAsArray() == 0).all())


class TestBuildMask(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # 600x600 at 30 m spans two 9 km cells across, half of it corn.
        self.src = str(fixtures.write_cdl_like(self.tmp / "cdl.tif", width=600, height=600, code=1))

    def test_writes_two_bands(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        self.assertEqual(gdal.Open(str(out)).RasterCount, 2)

    def test_fractions_stay_within_zero_and_one(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        data = gdal.Open(str(out)).ReadAsArray()
        finite = data[np.isfinite(data)]
        self.assertGreaterEqual(float(finite.min()), 0.0)
        self.assertLessEqual(float(finite.max()), 1.0)

    def test_band_sum_never_exceeds_one(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        data = np.nan_to_num(gdal.Open(str(out)).ReadAsArray())
        self.assertLessEqual(float((data[0] + data[1]).max()), 1.0 + 1e-6)

    def test_primary_band_reflects_actual_corn_cover(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        data = gdal.Open(str(out)).ReadAsArray()
        # The fixture is corn on its right half, so the covered cells average near 0.5.
        covered = data[0][data[0] > 0]
        self.assertGreater(float(covered.max()), 0.2)

    def test_double_crop_band_is_zero_when_no_double_crop_codes_present(self):
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        data = np.nan_to_num(gdal.Open(str(out)).ReadAsArray())
        self.assertAlmostEqual(float(data[1].max()), 0.0, places=6)

    def test_output_is_on_the_canonical_cpc_grid(self):
        from viz import gridmath
        out = prepare.build_mask(self.src, "corn", self.tmp / "mask.tif")
        ds = gdal.Open(str(out))
        gt = ds.GetGeoTransform()
        self.assertEqual((ds.RasterXSize, ds.RasterYSize),
                         (gridmath.CPC_GRID["width"], gridmath.CPC_GRID["height"]))
        self.assertAlmostEqual(gt[1], gridmath.CPC_GRID["pixel_x"], places=6)
        self.assertAlmostEqual(gt[5], gridmath.CPC_GRID["pixel_y"], places=6)


class TestScanMasks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_reports_only_years_with_a_complete_crop_set(self):
        for crop in naming.CROPS:
            (self.tmp / f"2024_{crop}_frac9km.tif").write_bytes(b"x")
        (self.tmp / "2025_corn_frac9km.tif").write_bytes(b"x")  # incomplete
        self.assertEqual(prepare.scan_masks(self.tmp), [2024])

    def test_empty_directory_yields_no_years(self):
        self.assertEqual(prepare.scan_masks(self.tmp), [])


class TestBuildCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cpc = self.tmp / "cpc"
        for rel in ("corn/cond/cornCond24w30.tif", "corn/prog/cornProg18w30.tif"):
            target = self.cpc / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
        self.cdl = self.tmp / "cdl"
        self.cdl.mkdir()
        fixtures.write_cdl_like(self.cdl / "2024_30m_cdls.tif")
        self.masks = self.tmp / "masks"
        self.masks.mkdir()

    def test_records_crops_variables_and_cdl_years(self):
        catalog = prepare.build_catalog(self.cpc, self.cdl, self.masks)
        self.assertEqual(tuple(catalog["crops"]), naming.CROPS)
        self.assertEqual(tuple(catalog["vars"]), naming.VARS)
        self.assertEqual(catalog["cdl_years"], [2024])

    def test_records_crop_codes_and_class_names(self):
        catalog = prepare.build_catalog(self.cpc, self.cdl, self.masks)
        self.assertEqual(catalog["crop_codes"]["corn"]["primary"], [1])
        self.assertEqual(catalog["cdl_classes"]["1"], "Corn")

    def test_pairs_every_cpc_year_with_a_cdl_year(self):
        catalog = prepare.build_catalog(self.cpc, self.cdl, self.masks)
        self.assertEqual(catalog["cdl_pairing"]["2018"], 2024)
        self.assertEqual(catalog["cdl_pairing"]["2024"], 2024)

    def test_mask_years_is_empty_when_no_masks_are_built(self):
        self.assertEqual(prepare.build_catalog(self.cpc, self.cdl, self.masks)["mask_years"], [])

    def test_mask_years_lists_years_with_a_complete_crop_set(self):
        for crop in naming.CROPS:
            (self.masks / f"2024_{crop}_frac9km.tif").write_bytes(b"x")
        self.assertEqual(
            prepare.build_catalog(self.cpc, self.cdl, self.masks)["mask_years"], [2024]
        )

    def test_serializes_to_json(self):
        catalog = prepare.build_catalog(self.cpc, self.cdl, self.masks)
        self.assertIsInstance(json.dumps(catalog), str)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_prepare -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz.prepare'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/prepare.py`:

```python
"""Catalog assembly and precomputation of the 9 km crop-fraction masks."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
from osgeo import gdal

from viz import gridmath, naming, paths, rasters

gdal.UseExceptions()

_CDL_TIF_RE = re.compile(r"^(?P<year>\d{4})_30m_cdls\.tif$")

MASK_KINDS = ("primary", "double")


def scan_cpc(cpc_root):
    """Index the extracted CPC rasters as crop -> var -> year -> sorted weeks."""
    found = {}
    for path in Path(cpc_root).rglob("*.tif"):
        parsed = naming.parse_cpc_filename(path.name)
        if parsed is None:
            continue
        crop = found.setdefault(parsed["crop"], {})
        var = crop.setdefault(parsed["var"], {})
        var.setdefault(str(parsed["year"]), []).append(parsed["week"])
    for crop in found.values():
        for var in crop.values():
            for year, weeks in var.items():
                var[year] = sorted(set(weeks))
    return found


def scan_cdl(cdl_root):
    """List the CDL years present as extracted rasters."""
    years = []
    for path in Path(cdl_root).glob("*.tif"):
        match = _CDL_TIF_RE.match(path.name)
        if match:
            years.append(int(match.group("year")))
    return sorted(years)


def build_lut_vrt(src_path, code_sets):
    """Build VRT XML exposing one Gray band per code set, each 0/1 through a LUT."""
    ds = gdal.Open(src_path)
    width, height = ds.RasterXSize, ds.RasterYSize
    geotransform = ", ".join(repr(v) for v in ds.GetGeoTransform())
    projection = ds.GetProjection()

    bands = []
    for index, codes in enumerate(code_sets):
        members = set(codes)
        lut = ",".join(f"{v}:{1 if v in members else 0}" for v in range(256))
        bands.append(
            f'<VRTRasterBand dataType="Byte" band="{index + 1}">'
            f"<ColorInterp>Gray</ColorInterp>"
            f"<ComplexSource>"
            f'<SourceFilename relativeToVRT="0">{src_path}</SourceFilename>'
            f"<SourceBand>1</SourceBand>"
            f'<SrcRect xOff="0" yOff="0" xSize="{width}" ySize="{height}"/>'
            f'<DstRect xOff="0" yOff="0" xSize="{width}" ySize="{height}"/>'
            f"<LUT>{lut}</LUT>"
            f"</ComplexSource></VRTRasterBand>"
        )
    return (
        f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">'
        f"<SRS>{projection}</SRS>"
        f"<GeoTransform>{geotransform}</GeoTransform>"
        f'{"".join(bands)}</VRTDataset>'
    )


def _warp_to_cpc_grid(vrt_xml):
    """Average-resample every VRT band onto the canonical CPC grid."""
    grid = gridmath.CPC_GRID
    minx, miny, maxx, maxy = gridmath.cpc_grid_bounds()
    src = gdal.Open(vrt_xml)
    out = gdal.Warp(
        "", src, format="MEM", dstSRS=grid["srs"],
        outputBounds=(minx, miny, maxx, maxy),
        width=grid["width"], height=grid["height"],
        resampleAlg=gdal.GRA_Average,
        # Overviews of a thematic raster are not class indicators, so averaging
        # from them would not give a true areal fraction. Force full resolution.
        overviewLevel="NONE",
        outputType=gdal.GDT_Float32, multithread=True,
    )
    data = out.ReadAsArray()
    return data if data.ndim == 3 else data[np.newaxis, ...]


def build_mask(src_path, crop, out_path):
    """Write the two-band crop-fraction mask for one crop from one CDL year."""
    codes = naming.CROP_CODES[crop]
    vrt = build_lut_vrt(src_path, [codes[kind] for kind in MASK_KINDS])
    data = np.clip(_warp_to_cpc_grid(vrt), 0.0, 1.0)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    return Path(rasters.write_multiband_float(out_path, data, gridmath.CPC_GRID))


def build_masks_for_year(src_path, year, mask_root):
    """Build all four crop masks for one CDL year in a single full-resolution pass."""
    code_sets = []
    for crop in naming.CROPS:
        for kind in MASK_KINDS:
            code_sets.append(naming.CROP_CODES[crop][kind])

    data = np.clip(_warp_to_cpc_grid(build_lut_vrt(src_path, code_sets)), 0.0, 1.0)

    written = []
    for index, crop in enumerate(naming.CROPS):
        pair = data[2 * index: 2 * index + 2]
        out = Path(mask_root) / f"{year}_{crop}_frac9km.tif"
        Path(mask_root).mkdir(parents=True, exist_ok=True)
        rasters.write_multiband_float(out, pair, gridmath.CPC_GRID)
        written.append(out)
    return written


def scan_masks(mask_root):
    """List CDL years that have a complete set of four crop masks on disk."""
    root = Path(mask_root)
    if not root.is_dir():
        return []
    years = {}
    for path in root.glob("*_frac9km.tif"):
        parts = path.stem.split("_")
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        years.setdefault(int(parts[0]), set()).add(parts[1])
    return sorted(y for y, crops in years.items() if crops >= set(naming.CROPS))


def build_catalog(cpc_root, cdl_root, mask_root):
    """Assemble the catalog the interface reads on load."""
    cdl_years = scan_cdl(cdl_root)
    cpc = scan_cpc(cpc_root)

    classes = {}
    if cdl_years:
        newest = Path(cdl_root) / f"{cdl_years[-1]}_30m_cdls.tif"
        classes = {str(k): v for k, v in rasters.read_rat(str(newest)).items()}

    cpc_years = sorted({int(y) for crop in cpc.values() for var in crop.values() for y in var})
    pairing = {
        str(year): naming.pair_cdl_year(year, cdl_years) for year in cpc_years
    } if cdl_years else {}

    return {
        "crops": list(naming.CROPS),
        "vars": list(naming.VARS),
        "var_labels": dict(naming.VAR_LABELS),
        "cpc": cpc,
        "cdl_years": cdl_years,
        "mask_years": scan_masks(mask_root),
        "cdl_classes": classes,
        "crop_codes": {
            crop: {kind: list(codes) for kind, codes in sets.items()}
            for crop, sets in naming.CROP_CODES.items()
        },
        "cdl_pairing": pairing,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the catalog and crop-fraction masks.")
    parser.add_argument("--skip-masks", action="store_true")
    parser.add_argument("--year", type=int, action="append",
                        help="Build masks for this CDL year only; repeatable.")
    args = parser.parse_args(argv)

    def write_catalog():
        catalog = build_catalog(paths.CPC_DATA, paths.CDL_DATA, paths.MASK_DATA)
        paths.CATALOG.parent.mkdir(parents=True, exist_ok=True)
        paths.CATALOG.write_text(json.dumps(catalog, indent=2))
        print(f"catalog: {paths.CATALOG} ({len(catalog['cpc'])} crops, "
              f"{len(catalog['cdl_years'])} CDL years, "
              f"masks for {catalog['mask_years'] or 'none'})", flush=True)
        return catalog

    catalog = write_catalog()

    if args.skip_masks:
        return 0

    # Masks default to 2024 and 2025; other years are opt-in through --year.
    years = args.year or [y for y in catalog["cdl_years"] if y in (2024, 2025)]
    for year in years:
        src = paths.CDL_DATA / f"{year}_30m_cdls.tif"
        print(f"masks {year}: full-resolution pass, this takes tens of minutes ...", flush=True)
        start = time.time()
        written = build_masks_for_year(str(src), year, paths.MASK_DATA)
        print(f"masks {year}: {len(written)} files in {(time.time() - start) / 60:.1f} min",
              flush=True)

    if years:
        write_catalog()  # refresh mask_years now that masks exist
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_prepare -v`

Expected: PASS, 18 tests

- [ ] **Step 5: Build the real catalog**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && ./run.sh prepare --skip-masks`

Expected: `catalog: .../data/catalog.json (4 crops, 4 CDL years)`. Then verify:

```bash
PYTHONNOUSERSITE=1 python3 -c "
import json; c = json.load(open('data/catalog.json'))
print('cdl years   :', c['cdl_years'])
print('mask years  :', c['mask_years'])
print('cpc years   :', sorted(c['cdl_pairing']))
print('2018 pairs  :', c['cdl_pairing']['2018'])
print('corn 2024 wk:', c['cpc']['corn']['cond']['2024'][:5], '...')
print('class 1     :', c['cdl_classes']['1'])
"
```

Expected: CDL years `[2022, 2023, 2024, 2025]`; mask years `[]` at this point; CPC years 2015 through 2026; `2018` pairs with `2022`; class 1 is `Corn`.

- [ ] **Step 6: Time one mask year before committing to all four**

Run one year first so the cost is measured rather than assumed. This may run for an hour or more, so start it in the background and check back:

```bash
cd /mnt/c/Users/emgonz38/Downloads/usda && \
  { time ./run.sh prepare --year 2024 ; } > /tmp/mask2024.log 2>&1 &
```

Expected: four files in `data/masks/`, and a wall time somewhere under the 144-minute projection measured against the zip. Verify the fractions are real:

```bash
PYTHONNOUSERSITE=1 python3 -c "
from osgeo import gdal; import numpy as np
d = gdal.Open('data/masks/2024_corn_frac9km.tif').ReadAsArray()
print('shape', d.shape)
print('primary  min/max/mean', float(np.nanmin(d[0])), float(np.nanmax(d[0])), float(np.nanmean(d[0])))
print('double   max', float(np.nanmax(d[1])))
print('sum <= 1 :', bool(np.nanmax(d[0] + d[1]) <= 1.0 + 1e-6))
"
```

Expected: shape `(2, 320, 508)`; primary max above 0.8 in the Corn Belt; band sum never above 1.

**Report the measured wall time before starting 2025.** If a single year comes in near the 144-minute projection, say so and let the user decide whether to continue, since the plan's scope is already narrowed to two years for exactly this reason.

- [ ] **Step 7: Build the 2025 masks**

Masks are scoped to 2024 and 2025. CDL 2022 and 2023 remain available as base layers with the mask control disabled, and can be added later with `./run.sh prepare --year 2022 --year 2023` and no code change.

```bash
cd /mnt/c/Users/emgonz38/Downloads/usda && ./run.sh prepare --year 2025 > /tmp/masks2025.log 2>&1 &
```

Expected on completion: `ls data/masks | wc -l` reports 8, and the catalog reports `mask_years` as `[2024, 2025]`:

```bash
PYTHONNOUSERSITE=1 python3 -c "
import json; print(json.load(open('data/catalog.json'))['mask_years'])"
```

- [ ] **Step 8: Commit**

```bash
git add viz/prepare.py tests/test_prepare.py
git commit -m "Add catalog assembly and 9 km crop-fraction mask construction"
```

---

### Task 7: Tile server

**Files:**
- Create: `viz/tileserver.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: everything from Tasks 1 and 3–6
- Produces:
  - `tileserver.render_cdl_tile(year, z, x, y) -> bytes`
  - `tileserver.render_cpc_tile(crop, var, year, week, z, x, y, mask_year=None) -> bytes`
  - `tileserver.point_report(lon, lat, crop, year, cdl_year) -> dict`
  - `tileserver.cache_path(key, z, x, y) -> Path`
  - `tileserver.make_server(port) -> ThreadingHTTPServer`
  - `tileserver.main(argv=None) -> int`

Routes, matching the spec exactly:

```
GET /                                                   viz/web/index.html
GET /static/<path>                                      viz/web/<path>
GET /api/catalog                                        data/catalog.json
GET /tiles/cdl/{year}/{z}/{x}/{y}.png
GET /tiles/cpc/{crop}/{var}/{year}/{week}/{z}/{x}/{y}.png[?mask={cdl_year}]
GET /api/point?lon=&lat=&crop=&year=&cdl_year=
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_server.py`:

```python
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

        cls.server = tileserver.make_server(0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

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
        status, ctype, body = self.get("/tiles/cdl/2024/7/30/47.png")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "image/png")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(_png_size(body), (256, 256))

    def test_cpc_tile_is_a_256_square_png(self):
        status, _, body = self.get("/tiles/cpc/corn/cond/2024/30/7/30/47.png")
        self.assertEqual(status, 200)
        self.assertEqual(_png_size(body), (256, 256))

    def test_masked_cpc_tile_renders(self):
        status, _, body = self.get("/tiles/cpc/corn/cond/2024/30/7/30/47.png?mask=2024")
        self.assertEqual(status, 200)
        self.assertEqual(_png_size(body), (256, 256))

    def test_masked_and_unmasked_tiles_differ(self):
        _, _, plain = self.get("/tiles/cpc/corn/cond/2024/30/7/30/47.png")
        _, _, masked = self.get("/tiles/cpc/corn/cond/2024/30/7/30/47.png?mask=2024")
        self.assertNotEqual(plain, masked)

    def test_second_request_is_served_from_the_disk_cache(self):
        self.get("/tiles/cdl/2024/8/61/94.png")
        cached = tileserver.cache_path("cdl-2024", 8, 61, 94)
        self.assertTrue(cached.is_file())

    def test_missing_cpc_week_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/cpc/corn/cond/2024/99/7/30/47.png")
        self.assertEqual(ctx.exception.code, 404)

    def test_missing_cdl_year_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/cdl/1999/7/30/47.png")
        self.assertEqual(ctx.exception.code, 404)

    def test_malformed_tile_coordinates_return_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/tiles/cdl/2024/a/b/c.png")
        self.assertIn(ctx.exception.code, (400, 404))


class TestPointRoute(ServerTestCase):
    def test_reports_cdl_class_and_crop_cover(self):
        status, ctype, body = self.get(
            "/api/point?lon=-93.62&lat=42.03&crop=corn&year=2024&cdl_year=2024")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        report = json.loads(body)
        for key in ("lon", "lat", "cdl_class", "cover", "series"):
            self.assertIn(key, report)
        self.assertIn("primary", report["cover"])
        self.assertIn("double", report["cover"])

    def test_series_carries_both_variables_keyed_by_week(self):
        _, _, body = self.get(
            "/api/point?lon=-93.62&lat=42.03&crop=corn&year=2024&cdl_year=2024")
        series = json.loads(body)["series"]
        self.assertIn("cond", series)
        self.assertIn("prog", series)

    def test_missing_parameters_return_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/point?lon=-93.62")
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'viz.tileserver'`

- [ ] **Step 3: Write minimal implementation**

Create `viz/tileserver.py`:

```python
"""Local tile server: routes, on-demand warping, and the disk tile cache."""

import argparse
import json
import re
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from viz import color, gridmath, naming, paths, rasters

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}

TILE_CDL_RE = re.compile(r"^/tiles/cdl/(?P<year>\d{4})/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$")
TILE_CPC_RE = re.compile(
    r"^/tiles/cpc/(?P<crop>[a-z]+)/(?P<var>cond|prog)/(?P<year>\d{4})/(?P<week>\d+)"
    r"/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$"
)

_palette_lock = threading.Lock()
_palettes = {}


def cdl_path(year):
    return paths.CDL_DATA / f"{year}_30m_cdls.tif"


def cpc_path(crop, var, year, week):
    return paths.CPC_DATA / naming.cpc_relpath(crop, var, year, week)


def mask_path(cdl_year, crop):
    return paths.MASK_DATA / f"{cdl_year}_{crop}_frac9km.tif"


def cdl_palette(year):
    """Palette lookup table for one CDL year, read once per process."""
    key = str(year)
    with _palette_lock:
        lut = _palettes.get(key)
    if lut is None:
        band = rasters.open_cached(str(cdl_path(year))).GetRasterBand(1)
        lut = color.palette_lut(band.GetRasterColorTable())
        with _palette_lock:
            _palettes[key] = lut
    return lut


def cache_path(key, z, x, y):
    return paths.TILE_CACHE / key / str(z) / str(x) / f"{y}.png"


def _cached(key, z, x, y, render):
    target = cache_path(key, z, x, y)
    if target.is_file():
        return target.read_bytes()
    blob = render()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".png.part")
    tmp.write_bytes(blob)
    tmp.replace(target)
    return blob


def render_cdl_tile(year, z, x, y):
    """One CDL tile: nearest-neighbour warp, then the source palette."""
    def render():
        codes = rasters.warp_tile(str(cdl_path(year)), z, x, y, resample="near")
        return rasters.encode_png(color.colorize_thematic(codes, cdl_palette(year)))

    return _cached(f"cdl-{year}", z, x, y, render)


def render_cpc_tile(crop, var, year, week, z, x, y, mask_year=None):
    """One CPC tile: bilinear warp, fixed ramp, optional crop-fraction alpha."""
    key = f"cpc-{crop}-{var}-{year}-{week}" + (f"-m{mask_year}" if mask_year else "")

    def render():
        values = rasters.warp_tile(
            str(cpc_path(crop, var, year, week)), z, x, y,
            resample="bilinear", dtype="float32",
        )
        alpha = None
        if mask_year is not None:
            fractions = rasters.warp_tile(
                str(mask_path(mask_year, crop)), z, x, y,
                resample="bilinear", bands=[1, 2], dtype="float32",
            )
            alpha = np.clip(np.nan_to_num(fractions[0]) + np.nan_to_num(fractions[1]), 0.0, 1.0)
        return rasters.encode_png(color.colorize_continuous(values, var, alpha=alpha))

    return _cached(key, z, x, y, render)


def point_report(lon, lat, crop, year, cdl_year):
    """CDL class, crop-cover split, and the weekly CPC series at one location."""
    x5070, y5070 = rasters.lonlat_to_5070(lon, lat)

    catalog = json.loads(paths.CATALOG.read_text())
    classes = catalog.get("cdl_classes", {})

    cdl_file = cdl_path(cdl_year)
    code = None
    if cdl_file.is_file():
        sampled = rasters.sample_point(str(cdl_file), x5070, y5070)[0]
        code = int(sampled) if sampled is not None else None

    cover = {"primary": None, "double": None}
    mask_file = mask_path(cdl_year, crop)
    if mask_file.is_file():
        primary, double = rasters.sample_point(str(mask_file), x5070, y5070, bands=[1, 2])
        cover = {"primary": primary, "double": double}

    series = {}
    for var in naming.VARS:
        weeks = catalog.get("cpc", {}).get(crop, {}).get(var, {}).get(str(year), [])
        points = []
        for week in weeks:
            path = cpc_path(crop, var, year, week)
            if not path.is_file():
                continue
            points.append({"week": week, "value": rasters.sample_point(str(path), x5070, y5070)[0]})
        series[var] = points

    return {
        "lon": lon,
        "lat": lat,
        "crop": crop,
        "year": year,
        "cdl_year": cdl_year,
        "cdl_code": code,
        "cdl_class": classes.get(str(code)) if code is not None else None,
        "cover": cover,
        "series": series,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "usda-viz/1.0"

    def log_message(self, fmt, *args):  # quieter than the default one line per tile
        pass

    def _send(self, body, content_type, status=HTTPStatus.OK, cache=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status, message):
        self.send_error(status, message)

    def _serve_file(self, path):
        if not path.is_file():
            return self._fail(HTTPStatus.NOT_FOUND, "not found")
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send(path.read_bytes(), ctype)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        try:
            if route == "/":
                return self._serve_file(paths.WEB / "index.html")

            if route.startswith("/static/"):
                relative = route[len("/static/"):]
                target = (paths.WEB / relative).resolve()
                if not str(target).startswith(str(paths.WEB.resolve())):
                    return self._fail(HTTPStatus.FORBIDDEN, "forbidden")
                return self._serve_file(target)

            if route == "/api/catalog":
                if not paths.CATALOG.is_file():
                    return self._fail(HTTPStatus.NOT_FOUND, "catalog not built; run prepare")
                return self._send(paths.CATALOG.read_bytes(), CONTENT_TYPES[".json"])

            if route == "/api/point":
                return self._handle_point(query)

            match = TILE_CDL_RE.match(route)
            if match:
                return self._handle_cdl_tile(match)

            match = TILE_CPC_RE.match(route)
            if match:
                return self._handle_cpc_tile(match, query)

            return self._fail(HTTPStatus.NOT_FOUND, "not found")
        except BrokenPipeError:
            pass
        except Exception as exc:  # a bad tile must not take the server down
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def _handle_cdl_tile(self, match):
        year = int(match.group("year"))
        if not cdl_path(year).is_file():
            return self._fail(HTTPStatus.NOT_FOUND, f"no CDL raster for {year}")
        z, x, y = (int(match.group(k)) for k in ("z", "x", "y"))
        self._send(render_cdl_tile(year, z, x, y), "image/png", cache=True)

    def _handle_cpc_tile(self, match, query):
        crop = match.group("crop")
        var = match.group("var")
        year = int(match.group("year"))
        week = int(match.group("week"))
        if crop not in naming.CROPS:
            return self._fail(HTTPStatus.NOT_FOUND, f"unknown crop {crop}")
        if not cpc_path(crop, var, year, week).is_file():
            return self._fail(HTTPStatus.NOT_FOUND, f"no {crop} {var} {year} week {week}")

        mask_year = None
        if "mask" in query:
            mask_year = int(query["mask"][0])
            if not mask_path(mask_year, crop).is_file():
                return self._fail(HTTPStatus.NOT_FOUND, f"no mask for {crop} {mask_year}")

        z, x, y = (int(match.group(k)) for k in ("z", "x", "y"))
        self._send(render_cpc_tile(crop, var, year, week, z, x, y, mask_year),
                   "image/png", cache=True)

    def _handle_point(self, query):
        required = ("lon", "lat", "crop", "year", "cdl_year")
        if any(key not in query for key in required):
            return self._fail(HTTPStatus.BAD_REQUEST, f"required: {', '.join(required)}")
        report = point_report(
            float(query["lon"][0]), float(query["lat"][0]), query["crop"][0],
            int(query["year"][0]), int(query["cdl_year"][0]),
        )
        self._send(json.dumps(report).encode(), CONTENT_TYPES[".json"])


def make_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the CPC-over-CDL map locally.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if not paths.CATALOG.is_file():
        print("catalog.json missing; run ./run.sh prepare first", file=sys.stderr)
        return 1

    server = make_server(args.port)
    host, port = server.server_address
    print(f"serving on http://{host}:{port}/  (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server -v`

Expected: PASS, 15 tests. Task 8 creates `viz/web/index.html`; until then `test_root_serves_html` fails, so create a one-line placeholder now and let Task 8 replace it:

```bash
mkdir -p viz/web && printf '<html><body>placeholder</body></html>\n' > viz/web/index.html
```

- [ ] **Step 5: Commit**

```bash
git add viz/tileserver.py tests/test_server.py viz/web/index.html
git commit -m "Add tile server with disk cache, masked rendering, and point queries"
```

---

### Task 8: Browser interface

**Files:**
- Create: `viz/web/app.js`, `viz/web/style.css`, `viz/web/vendor/leaflet/leaflet.js`, `viz/web/vendor/leaflet/leaflet.css`, `viz/web/vendor/leaflet/images/*`
- Modify: `viz/web/index.html` (replaces the Task 7 placeholder)

**Interfaces:**
- Consumes: `/api/catalog`, `/tiles/cdl/...`, `/tiles/cpc/...`, `/api/point`
- Produces: the interface itself

Overlay and swipe are exclusive view modes; crop masking is an independent checkbox that composes with either, giving four valid combinations; click-to-inspect is always live rather than a mode. The default view is corn condition for 2025 at week 30 over CDL 2025, at CONUS extent, in overlay mode at 70% opacity.

- [ ] **Step 1: Vendor Leaflet**

This is the only network access in the whole project. Run it once:

```bash
cd /mnt/c/Users/emgonz38/Downloads/usda && mkdir -p viz/web/vendor/leaflet/images && \
  curl -fsSL -o viz/web/vendor/leaflet/leaflet.js  https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js && \
  curl -fsSL -o viz/web/vendor/leaflet/leaflet.css https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css && \
  for img in marker-icon.png marker-icon-2x.png marker-shadow.png layers.png layers-2x.png; do \
    curl -fsSL -o "viz/web/vendor/leaflet/images/$img" "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/$img"; \
  done && ls -la viz/web/vendor/leaflet viz/web/vendor/leaflet/images
```

Expected: `leaflet.js` around 147 KB, `leaflet.css` around 15 KB, five images.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_server.py`:

```python
class TestInterfaceAssets(ServerTestCase):
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server.TestInterfaceAssets -v`

Expected: FAIL — the placeholder `index.html` has no Leaflet reference, and `app.js` and `style.css` do not exist.

- [ ] **Step 4: Write `viz/web/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>USDA Crop Progress and Condition over the Cropland Data Layer</title>
<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css">
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div id="map"></div>

<aside id="panel">
  <h1>CPC over CDL</h1>

  <section class="controls">
    <label>Crop
      <select id="crop"></select>
    </label>
    <label>Variable
      <select id="var"></select>
    </label>
    <label>CPC year
      <select id="year"></select>
    </label>
    <label>CDL year
      <select id="cdlYear"></select>
    </label>
  </section>

  <section class="controls">
    <label>Week <output id="weekOut"></output>
      <input type="range" id="week" min="0" max="0" step="1">
    </label>
    <div class="row">
      <button id="play" type="button">Play</button>
      <button id="prev" type="button">&larr;</button>
      <button id="next" type="button">&rarr;</button>
    </div>
  </section>

  <section class="controls">
    <fieldset>
      <legend>View</legend>
      <label class="inline"><input type="radio" name="mode" value="overlay" checked> Overlay</label>
      <label class="inline"><input type="radio" name="mode" value="swipe"> Swipe</label>
    </fieldset>
    <label class="inline"><input type="checkbox" id="mask"> Mask to crop extent</label>
    <label>Opacity <output id="opacityOut">70%</output>
      <input type="range" id="opacity" min="0" max="100" step="5" value="70">
    </label>
  </section>

  <section id="legend"></section>
  <p id="pairing" class="note"></p>
  <section id="readout"><p class="note">Click the map to inspect a cell.</p></section>
</aside>

<script src="/static/vendor/leaflet/leaflet.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 5: Write `viz/web/style.css`**

```css
:root {
  --panel-bg: #ffffff;
  --panel-fg: #1c1c1c;
  --muted: #63666a;
  --line: #d8d8d4;
  --accent: #1a6e2f;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  height: 100%;
  font: 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  color: var(--panel-fg);
}

#map { position: absolute; inset: 0 340px 0 0; }

#panel {
  position: absolute;
  top: 0; right: 0; bottom: 0;
  width: 340px;
  padding: 16px;
  overflow-y: auto;
  background: var(--panel-bg);
  border-left: 1px solid var(--line);
}

h1 { margin: 0 0 14px; font-size: 15px; font-weight: 650; letter-spacing: -0.01em; }

.controls {
  display: grid;
  gap: 9px;
  padding-bottom: 13px;
  margin-bottom: 13px;
  border-bottom: 1px solid var(--line);
}

label { display: grid; gap: 3px; font-weight: 550; }
label.inline { display: flex; align-items: center; gap: 6px; font-weight: 450; }
label.inline:has(input:disabled) { color: var(--muted); cursor: not-allowed; }

select, input[type="range"] { width: 100%; }
select { padding: 4px; border: 1px solid var(--line); border-radius: 3px; background: #fff; }

fieldset { border: 1px solid var(--line); border-radius: 3px; padding: 7px 9px; margin: 0; }
legend { padding: 0 4px; font-weight: 550; }

.row { display: flex; gap: 6px; }
.row button { flex: 1; padding: 4px; border: 1px solid var(--line); border-radius: 3px;
              background: #fff; cursor: pointer; font: inherit; }
.row button:hover { border-color: var(--accent); }

output { font-variant-numeric: tabular-nums; font-weight: 450; color: var(--muted); }

.note { color: var(--muted); margin: 8px 0 0; }

#legend .swatches { display: flex; height: 13px; border-radius: 2px; overflow: hidden; }
#legend .swatches span { flex: 1; }
#legend .labels { display: flex; justify-content: space-between;
                  color: var(--muted); font-size: 11px; margin-top: 3px; }

#readout table { width: 100%; border-collapse: collapse; margin-top: 7px; }
#readout th { text-align: left; font-weight: 550; padding: 2px 0; }
#readout td { text-align: right; font-variant-numeric: tabular-nums; padding: 2px 0; }
#readout svg { width: 100%; height: 72px; margin-top: 9px; }

/* Swipe mode: the CPC pane is clipped to the right of the divider. */
#swipeHandle {
  position: absolute; top: 0; bottom: 0; width: 3px;
  background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.35);
  cursor: ew-resize; z-index: 700;
}
#swipeHandle::after {
  content: ""; position: absolute; top: 50%; left: 50%;
  width: 26px; height: 26px; margin: -13px 0 0 -13px; border-radius: 50%;
  background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.35);
}
```

- [ ] **Step 6: Write `viz/web/app.js`**

```javascript
/* Interface for the local CPC-over-CDL map. All data comes from this server. */
(function () {
  "use strict";

  var state = {
    catalog: null,
    crop: "corn",
    var: "cond",
    year: 2025,
    week: 30,
    cdlYear: 2025,
    mode: "overlay",
    mask: false,
    opacity: 0.7,
    playing: false,
    timer: null
  };

  var map = L.map("map", { center: [39.5, -96.0], zoom: 4, minZoom: 3, maxZoom: 15 });
  map.createPane("cdl");
  map.createPane("cpc");
  map.getPane("cdl").style.zIndex = 400;
  map.getPane("cpc").style.zIndex = 450;

  var cdlLayer = null;
  var cpcLayer = null;
  var marker = null;
  var swipeHandle = null;
  var swipeFraction = 0.5;

  function el(id) { return document.getElementById(id); }

  function weeksFor(crop, variable, year) {
    var byVar = (state.catalog.cpc[crop] || {})[variable] || {};
    return byVar[String(year)] || [];
  }

  function yearsFor(crop, variable) {
    return Object.keys((state.catalog.cpc[crop] || {})[variable] || {})
      .map(Number).sort(function (a, b) { return a - b; });
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function fillSelect(node, values, selected, labeller) {
    node.innerHTML = "";
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = labeller ? labeller(value) : value;
      if (String(value) === String(selected)) { option.selected = true; }
      node.appendChild(option);
    });
  }

  function drawCdl() {
    if (cdlLayer) { map.removeLayer(cdlLayer); }
    cdlLayer = L.tileLayer("/tiles/cdl/" + state.cdlYear + "/{z}/{x}/{y}.png", {
      pane: "cdl", maxNativeZoom: 15, maxZoom: 15, noWrap: true,
      attribution: "USDA NASS Cropland Data Layer " + state.cdlYear
    }).addTo(map);
  }

  function drawCpc() {
    if (cpcLayer) { map.removeLayer(cpcLayer); }
    var url = "/tiles/cpc/" + state.crop + "/" + state.var + "/" + state.year +
              "/" + state.week + "/{z}/{x}/{y}.png" +
              (state.mask ? "?mask=" + state.cdlYear : "");
    cpcLayer = L.tileLayer(url, {
      pane: "cpc", maxNativeZoom: 15, maxZoom: 15, noWrap: true,
      opacity: state.opacity,
      attribution: "USDA NASS Crop Progress and Condition " + state.year
    }).addTo(map);
    applyMode();
  }

  function applyMode() {
    var pane = map.getPane("cpc");
    if (state.mode === "swipe") {
      if (!swipeHandle) {
        swipeHandle = document.createElement("div");
        swipeHandle.id = "swipeHandle";
        map.getContainer().appendChild(swipeHandle);
        var dragging = false;
        swipeHandle.addEventListener("mousedown", function (e) {
          dragging = true; e.preventDefault(); map.dragging.disable();
        });
        document.addEventListener("mousemove", function (e) {
          if (!dragging) { return; }
          var box = map.getContainer().getBoundingClientRect();
          swipeFraction = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
          positionSwipe();
        });
        document.addEventListener("mouseup", function () {
          dragging = false; map.dragging.enable();
        });
      }
      swipeHandle.style.display = "block";
      pane.style.opacity = 1;
      positionSwipe();
    } else {
      if (swipeHandle) { swipeHandle.style.display = "none"; }
      pane.style.clipPath = "";
      pane.style.opacity = 1;
      if (cpcLayer) { cpcLayer.setOpacity(state.opacity); }
    }
  }

  function positionSwipe() {
    var width = map.getContainer().clientWidth;
    var x = Math.round(width * swipeFraction);
    swipeHandle.style.left = x + "px";
    map.getPane("cpc").style.clipPath = "inset(0 0 0 " + x + "px)";
    if (cpcLayer) { cpcLayer.setOpacity(1); }
  }

  function drawLegend() {
    var stops = state.var === "cond"
      ? [{ v: 1, l: "Very poor" }, { v: 2, l: "Poor" }, { v: 3, l: "Fair" },
         { v: 4, l: "Good" }, { v: 5, l: "Excellent" }]
      : [{ v: 0, l: "0%" }, { v: 0.5, l: "50%" }, { v: 1, l: "100%" }];
    var ramp = state.var === "cond"
      ? ["#a50f15", "#de5c37", "#f7e08f", "#78b75c", "#1a6e2f"]
      : ["#f7f4e9", "#c4d6ac", "#6ea694", "#194a6e"];

    el("legend").innerHTML =
      '<div class="swatches">' +
      ramp.map(function (c) { return '<span style="background:' + c + '"></span>'; }).join("") +
      "</div><div class=\"labels\">" +
      stops.map(function (s) { return "<span>" + s.l + "</span>"; }).join("") +
      "</div>";
  }

  function drawPairing() {
    var paired = state.catalog.cdl_pairing[String(state.year)];
    var text = "CPC " + state.year + " week " + state.week + " over CDL " + state.cdlYear;
    if (Number(state.cdlYear) !== Number(state.year)) {
      text += " (no CDL for " + state.year + "; nearest is " + paired + ")";
    }
    if (!maskAvailable()) {
      text += ". No crop mask built for CDL " + state.cdlYear + ".";
    }
    el("pairing").textContent = text;
  }

  function maskAvailable() {
    return (state.catalog.mask_years || []).indexOf(Number(state.cdlYear)) >= 0;
  }

  function syncMaskControl() {
    var box = el("mask");
    var available = maskAvailable();
    box.disabled = !available;
    box.parentNode.title = available
      ? "Scale opacity by the fraction of each 9 km cell growing this crop"
      : "Masks are built for CDL 2024 and 2025 only";
    if (!available && state.mask) { state.mask = false; box.checked = false; }
  }

  function sparkline(series, domainLo, domainHi) {
    var points = series.filter(function (p) { return p.value !== null; });
    if (points.length < 2) { return ""; }
    var weeks = points.map(function (p) { return p.week; });
    var w0 = Math.min.apply(null, weeks), w1 = Math.max.apply(null, weeks);
    var path = points.map(function (p, i) {
      var x = (p.week - w0) / (w1 - w0 || 1) * 100;
      var y = 30 - (p.value - domainLo) / (domainHi - domainLo) * 28;
      return (i ? "L" : "M") + x.toFixed(2) + " " + y.toFixed(2);
    }).join(" ");
    return '<svg viewBox="0 0 100 32" preserveAspectRatio="none">' +
           '<path d="' + path + '" fill="none" stroke="#1a6e2f" stroke-width="1.2"' +
           ' vector-effect="non-scaling-stroke"/></svg>';
  }

  function pct(value) {
    return value === null || value === undefined ? "—" : (value * 100).toFixed(1) + "%";
  }

  function showReadout(report) {
    var cover = report.cover || {};
    var total = (cover.primary || 0) + (cover.double || 0);
    var html =
      "<h2>" + (report.cdl_class || "Unknown") + "</h2>" +
      "<table>" +
      "<tr><th>" + report.crop + " cover</th><td>" + pct(total) + "</td></tr>" +
      "<tr><th>&nbsp;&nbsp;primary</th><td>" + pct(cover.primary) + "</td></tr>" +
      "<tr><th>&nbsp;&nbsp;double-crop</th><td>" + pct(cover.double) + "</td></tr>" +
      "</table>" +
      "<p class=\"note\">Condition, weeks " + state.year + "</p>" +
      sparkline(report.series.cond || [], 1, 5) +
      "<p class=\"note\">Progress, weeks " + state.year + "</p>" +
      sparkline(report.series.prog || [], 0, 1);
    el("readout").innerHTML = html;
  }

  function refresh() {
    syncMaskControl();
    drawCdl();
    drawCpc();
    drawLegend();
    drawPairing();
    el("weekOut").textContent = "w" + pad(state.week);
  }

  function syncWeekSlider() {
    var weeks = weeksFor(state.crop, state.var, state.year);
    var slider = el("week");
    slider.min = 0;
    slider.max = Math.max(0, weeks.length - 1);
    var index = weeks.indexOf(state.week);
    if (index < 0) {
      index = Math.min(weeks.length - 1, Math.floor(weeks.length / 2));
      state.week = weeks[index];
    }
    slider.value = index;
    slider.dataset.weeks = JSON.stringify(weeks);
  }

  function syncYearSelect() {
    var years = yearsFor(state.crop, state.var);
    if (years.indexOf(state.year) < 0) { state.year = years[years.length - 1]; }
    fillSelect(el("year"), years, state.year);
    state.cdlYear = state.catalog.cdl_pairing[String(state.year)];
    fillSelect(el("cdlYear"), state.catalog.cdl_years, state.cdlYear);
  }

  function stepWeek(delta) {
    var weeks = JSON.parse(el("week").dataset.weeks || "[]");
    if (!weeks.length) { return; }
    var index = (weeks.indexOf(state.week) + delta + weeks.length) % weeks.length;
    state.week = weeks[index];
    el("week").value = index;
    refresh();
  }

  function wire() {
    el("crop").addEventListener("change", function (e) {
      state.crop = e.target.value; syncYearSelect(); syncWeekSlider(); refresh();
    });
    el("var").addEventListener("change", function (e) {
      state.var = e.target.value; syncYearSelect(); syncWeekSlider(); refresh();
    });
    el("year").addEventListener("change", function (e) {
      state.year = Number(e.target.value);
      state.cdlYear = state.catalog.cdl_pairing[String(state.year)];
      fillSelect(el("cdlYear"), state.catalog.cdl_years, state.cdlYear);
      syncWeekSlider(); refresh();
    });
    el("cdlYear").addEventListener("change", function (e) {
      state.cdlYear = Number(e.target.value); refresh();
    });
    el("week").addEventListener("input", function (e) {
      var weeks = JSON.parse(e.target.dataset.weeks || "[]");
      state.week = weeks[Number(e.target.value)];
      refresh();
    });
    el("prev").addEventListener("click", function () { stepWeek(-1); });
    el("next").addEventListener("click", function () { stepWeek(1); });
    el("play").addEventListener("click", function () {
      state.playing = !state.playing;
      el("play").textContent = state.playing ? "Pause" : "Play";
      if (state.playing) {
        state.timer = setInterval(function () { stepWeek(1); }, 700);
      } else {
        clearInterval(state.timer);
      }
    });
    el("opacity").addEventListener("input", function (e) {
      state.opacity = Number(e.target.value) / 100;
      el("opacityOut").textContent = e.target.value + "%";
      if (cpcLayer && state.mode === "overlay") { cpcLayer.setOpacity(state.opacity); }
    });
    el("mask").addEventListener("change", function (e) {
      state.mask = e.target.checked; drawCpc();
    });
    Array.prototype.forEach.call(document.getElementsByName("mode"), function (radio) {
      radio.addEventListener("change", function (e) {
        if (e.target.checked) { state.mode = e.target.value; applyMode(); }
      });
    });

    map.on("click", function (e) {
      if (marker) { map.removeLayer(marker); }
      marker = L.circleMarker(e.latlng, { radius: 5, color: "#fff", weight: 2,
                                          fillColor: "#111", fillOpacity: 1 }).addTo(map);
      el("readout").innerHTML = '<p class="note">Reading…</p>';
      var url = "/api/point?lon=" + e.latlng.lng.toFixed(6) +
                "&lat=" + e.latlng.lat.toFixed(6) +
                "&crop=" + state.crop + "&year=" + state.year + "&cdl_year=" + state.cdlYear;
      fetch(url).then(function (r) { return r.json(); }).then(showReadout)
        .catch(function () { el("readout").innerHTML = '<p class="note">Read failed.</p>'; });
    });

    map.on("resize", function () { if (state.mode === "swipe") { positionSwipe(); } });
  }

  fetch("/api/catalog").then(function (r) { return r.json(); }).then(function (catalog) {
    state.catalog = catalog;
    fillSelect(el("crop"), catalog.crops, state.crop);
    fillSelect(el("var"), catalog.vars, state.var, function (v) { return catalog.var_labels[v]; });
    syncYearSelect();
    syncWeekSlider();
    wire();
    refresh();
  });
})();
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && PYTHONNOUSERSITE=1 python3 -m unittest tests.test_server -v`

Expected: PASS, 19 tests

- [ ] **Step 8: Run the whole suite**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && ./run.sh test`

Expected: PASS, roughly 100 tests, zero failures and zero errors.

- [ ] **Step 9: Verify in the browser**

Run: `cd /mnt/c/Users/emgonz38/Downloads/usda && ./run.sh serve`

Open `http://127.0.0.1:8000/` and confirm each of these:

1. The CDL base draws at CONUS extent in the USDA palette, with corn a saturated yellow across Iowa and Illinois.
2. Corn condition for 2025 week 30 draws over it at 70% opacity; the caption reads `CPC 2025 week 30 over CDL 2025`.
3. Dragging the week slider redraws; **Play** animates and **Pause** stops it.
4. Switching CPC year to 2018 changes the caption to note that no CDL exists for 2018 and 2022 is nearest.
5. With CDL year 2024 or 2025 selected, ticking **Mask to crop extent** confines the condition layer to where corn is grown, fading out across the Great Plains. Switching CDL year to 2022 or 2023 disables that checkbox and the caption notes that no mask is built for that year.
6. Selecting **Swipe** shows a draggable divider with CDL to its left and CPC to its right.
7. Clicking near Ames, Iowa reports a CDL class with a corn cover split into primary and double-crop, plus two sparklines.
8. Zooming to 14 over a field resolves individual 30 m CDL pixels.

- [ ] **Step 10: Commit**

```bash
git add viz/web/
git commit -m "Add Leaflet interface with overlay, swipe, masking, and point inspection"
```

---

## Self-Review

**Spec coverage.** Section 2 (source data) informs Tasks 2 and 6. Section 3 (environment) is a Global Constraint enforced by `run.sh` in Task 2. Section 4.1 (layout) is Task 1's `paths.py`. Section 5 (extraction) is Task 2. Section 6.1 (catalog), 6.2 (crop codes), 6.3 (pairing), and 6.4 (masks) are Tasks 1 and 6. Section 7.1 (endpoints), 7.2 (tile pipeline), 7.3 (masked rendering), and 7.4 (point query) are Tasks 5 and 7. Section 8 (interface) is Task 8. Section 9 (testing) is distributed across every task. Section 10 (out of scope) is a Global Constraint. No gaps.

**Placeholder scan.** Every code step carries complete, runnable content. No "TBD", no "handle edge cases", no "similar to Task N".

**Type consistency.** `warp_tile` is called with `resample=`, `bands=`, and `dtype=` in Tasks 5 and 7, matching its Task 5 definition. `colorize_continuous(values, var, alpha=)` and `colorize_thematic(codes, lut)` match between Tasks 4 and 7. `write_multiband_float(path, data, grid)` is defined in Task 5 and used in Task 6. `sample_point(path, x, y, bands=)` returns a list in both Tasks 5 and 7. `cpc_relpath` is defined in Task 1 and used in Tasks 2, 6, and 7. The catalog keys written in Task 6 (`crops`, `vars`, `var_labels`, `cpc`, `cdl_years`, `cdl_classes`, `crop_codes`, `cdl_pairing`) are exactly those read in Tasks 7 and 8.
