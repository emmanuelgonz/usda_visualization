"""One-time, idempotent extraction of the CPC and CDL archives to a flat layout.

The CPC archives nest twice: the yearly zip holds one directory per crop, and
each of those holds a single inner zip whose condition/ and progress/
directories hold the weekly rasters.
"""

import argparse
import io
import re
import shutil
import sys
import zipfile

from viz import naming, paths

CDL_KEEP_SUFFIXES = (".tif", ".tif.ovr", ".aux", ".tfw")
_CDL_YEAR_RE = re.compile(r"^(?P<year>\d{4})_30m_cdls\.zip$")
_CPC_YEAR_RE = re.compile(r"^cpc(?P<year>\d{4})\.zip$")

_COPY_CHUNK = 16 * 1024 * 1024


def _extract_member(zf, member, target):
    """Stream one zip member to target unless it already matches by size.

    Compares against ZipInfo.file_size (the uncompressed size) before reading
    anything, so an idempotent re-run never decompresses a member it is about
    to skip, and never holds a whole member in memory when it does write.
    """
    info = zf.getinfo(member)
    if target.exists() and target.stat().st_size == info.file_size:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst, _COPY_CHUNK)
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
                    if _extract_member(inner, member, target):
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
            if _extract_member(zf, member, dest / leaf):
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
