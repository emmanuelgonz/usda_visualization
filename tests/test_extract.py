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
