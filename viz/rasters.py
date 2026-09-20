"""GDAL access: thread-local dataset caching, tile warping, and point sampling."""

import math
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
    if bands is None and src.RasterCount > 1:
        raise ValueError(f"{path} has {src.RasterCount} bands; pass bands= explicitly")
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
    col = math.floor((x5070 - gt[0]) / gt[1])
    row = math.floor((y5070 - gt[3]) / gt[5])
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


def lonlat_to_5070(lon, lat):
    """Transform WGS84 longitude and latitude to NAD83 / Conus Albers.

    OGRCoordinateTransformation is not thread-safe, so the transform is built
    once per thread and cached on the same thread-local used for datasets.
    """
    transform = getattr(_local, "to_5070", None)
    if transform is None:
        source = osr.SpatialReference()
        source.ImportFromEPSG(4326)
        source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        target = osr.SpatialReference()
        target.ImportFromEPSG(5070)
        target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transform = _local.to_5070 = osr.CoordinateTransformation(source, target)
    x, y, _ = transform.TransformPoint(float(lon), float(lat))
    return (x, y)


def encode_png(rgba):
    """Encode an (H, W, 4) uint8 array as PNG bytes through GDAL's PNG driver."""
    height, width = rgba.shape[:2]
    mem = gdal.GetDriverByName("MEM").Create("", width, height, 4, gdal.GDT_Byte)
    for index in range(4):
        mem.GetRasterBand(index + 1).WriteArray(rgba[..., index])

    name = f"/vsimem/tile_{threading.get_ident()}_{id(rgba)}.png"
    gdal.GetDriverByName("PNG").CreateCopy(name, mem)
    try:
        handle = gdal.VSIFOpenL(name, "rb")
        gdal.VSIFSeekL(handle, 0, 2)
        size = gdal.VSIFTellL(handle)
        gdal.VSIFSeekL(handle, 0, 0)
        blob = gdal.VSIFReadL(1, size, handle)
        gdal.VSIFCloseL(handle)
        return blob
    finally:
        gdal.Unlink(name)


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
