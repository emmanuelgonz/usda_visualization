"""Catalog assembly and precomputation of the 9 km crop-fraction masks."""

import argparse
import json
import os
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
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    rasters.write_multiband_float(part, data, gridmath.CPC_GRID)
    os.replace(part, out)
    return out


def build_masks_for_year(src_path, year, mask_root):
    """Build all four crop masks for one CDL year in a single full-resolution pass."""
    code_sets = []
    for crop in naming.CROPS:
        for kind in MASK_KINDS:
            code_sets.append(naming.CROP_CODES[crop][kind])

    data = np.clip(_warp_to_cpc_grid(build_lut_vrt(src_path, code_sets)), 0.0, 1.0)

    written = []
    Path(mask_root).mkdir(parents=True, exist_ok=True)
    for index, crop in enumerate(naming.CROPS):
        pair = data[2 * index: 2 * index + 2]
        out = Path(mask_root) / f"{year}_{crop}_frac9km.tif"
        part = out.with_name(out.name + ".part")
        rasters.write_multiband_float(part, pair, gridmath.CPC_GRID)
        os.replace(part, out)
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
