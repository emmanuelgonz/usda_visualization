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


def write_cdl_like(path, width=40, height=30, code=1, pixel=300.0):
    """A paletted Byte EPSG:5070 raster carrying a color table and an attribute table.

    The default 300 m pixel is not the CDL's real 30 m. At 30 m a 40 x 30 fixture
    spans 1.2 x 0.9 km, which is sub-pixel inside a 313 km zoom-7 tile and would
    make every warp test pass vacuously against empty space. 300 m spans
    12 x 9 km, and the larger fixtures used by the mask tests then cover enough
    9 km CPC cells to produce meaningful fractions.
    """
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

    # Real CDL rasters carry a dense, 256-row RAT where row index equals pixel
    # value (row 5 is Soybeans), not a compacted table indexed by row order.
    # Rows are indexed by value here to match, so read_rat's row-as-code
    # convention resolves the same way against the fixture as against real data.
    rat = gdal.RasterAttributeTable()
    rat.CreateColumn("Count", gdal.GFT_Integer, gdal.GFU_PixelCount)
    rat.CreateColumn("Class_Name", gdal.GFT_String, gdal.GFU_Name)
    classes = {0: "Background", 1: "Corn", 5: "Soybeans"}
    rat.SetRowCount(max(classes) + 1)
    for value, name in classes.items():
        rat.SetValueAsInt(value, 0, int((data == value).sum()))
        rat.SetValueAsString(value, 1, name)
    band.SetDefaultRAT(rat)

    band.WriteArray(data)
    ds.FlushCache()
    return path


def fixture_lonlat(path):
    """WGS84 longitude and latitude of a fixture raster's centre."""
    ds = gdal.Open(str(path))
    gt = ds.GetGeoTransform()
    x = gt[0] + gt[1] * ds.RasterXSize / 2.0
    y = gt[3] + gt[5] * ds.RasterYSize / 2.0

    source = osr.SpatialReference()
    source.ImportFromEPSG(5070)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    lon, lat, _ = osr.CoordinateTransformation(source, target).TransformPoint(x, y)
    return (lon, lat)


def tile_covering(path, zoom=9):
    """The (z, x, y) tile containing a fixture's centre.

    Computed from the raster's own geotransform rather than hardcoded: the
    fixtures sit at the canonical CPC grid origin, which is 127.35 W 48.20 N,
    not anywhere in the Corn Belt.
    """
    lon, lat = fixture_lonlat(path)
    x, y = gridmath.lonlat_to_tile(lon, lat, zoom)
    return (zoom, x, y)


def tile_far_from(path, zoom=9):
    """A tile at the same zoom that cannot overlap the fixture."""
    _, x, y = tile_covering(path, zoom)
    return (zoom, (x + 2 ** zoom // 2) % (2 ** zoom), y)
