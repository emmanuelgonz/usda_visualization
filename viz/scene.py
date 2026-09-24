"""EMIT scene overlays: the browse image cache and the georeferenced tile render.

A scene's browse PNG is in swath geometry, so it is placed with four ground
control points (its pixel corners onto the footprint vertices in image
order, see viz.emit.scene_corners) and warped per Web Mercator tile through
the same disk tile cache as the CDL and CPC layers. The download is the
project's only on-demand network access and is confined to the LP DAAC host.
"""

import math
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from viz import gridmath, paths, rasters

gdal.UseExceptions()

BROWSE_HOST = "data.lpdaac.earthdatacloud.nasa.gov"


class BrowseError(Exception):
    """The browse image could not be obtained."""


def browse_path(scene_id):
    return paths.SCENE_CACHE / f"{scene_id}.png"


def download(url, dest):
    """Fetch url into dest. Raises BrowseError on any network or HTTP failure."""
    request = urllib.request.Request(url, headers={"User-Agent": "usda-viz"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            Path(dest).write_bytes(response.read())
    except OSError as exc:
        raise BrowseError(str(exc)) from exc


_download_locks = {}
_download_locks_guard = threading.Lock()


def _lock_for(scene_id):
    with _download_locks_guard:
        return _download_locks.setdefault(scene_id, threading.Lock())


def ensure_browse(scene_id, url, fetch=download):
    """The cached browse file for a scene, downloading it once if absent.

    Only the final file counts as cached; a leftover .part from an interrupted
    download is overwritten. Concurrent callers for one scene download once.
    """
    target = browse_path(scene_id)
    if target.is_file():
        return target
    if not url or urllib.parse.urlparse(url).hostname != BROWSE_HOST:
        raise BrowseError(f"no browse image on {BROWSE_HOST} for {scene_id}")
    with _lock_for(scene_id):
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        try:
            fetch(url, part)
            os.replace(part, target)
        except BrowseError:
            part.unlink(missing_ok=True)
            raise
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise BrowseError(str(exc)) from exc
    return target


_sources = {}
_sources_guard = threading.Lock()


def gcp_source(scene_id, corners):
    """Name of an in-memory VRT placing the browse PNG by its four corners, built once per process.

    Built under the scene's own download lock so concurrent first requests for
    one scene don't race to write (or read mid-write) the same /vsimem name.
    """
    with _sources_guard:
        name = _sources.get(scene_id)
    if name is not None:
        return name
    with _lock_for(scene_id):
        with _sources_guard:
            name = _sources.get(scene_id)
        if name is not None:
            return name
        png = str(browse_path(scene_id))
        src = gdal.Open(png)
        width, height = src.RasterXSize, src.RasterYSize
        bands = [1, 2, 3] if src.RasterCount >= 3 else [1, 1, 1]
        pixels = [(0, 0), (width, 0), (width, height), (0, height)]
        gcps = [gdal.GCP(float(lon), float(lat), 0.0, float(px), float(py))
                for (lon, lat), (px, py) in zip(corners, pixels)]
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        name = f"/vsimem/scene_{scene_id}.vrt"
        gdal.Translate(name, src, format="VRT", bandList=bands, GCPs=gcps, outputSRS=srs.ExportToWkt())
        with _sources_guard:
            _sources[scene_id] = name
    return name


def forget_all():
    """Drop the per-process VRTs (tests swap the cache directory)."""
    with _sources_guard:
        for name in _sources.values():
            try:
                gdal.Unlink(name)
            except RuntimeError:
                pass
        _sources.clear()


def tile_lonlat_bounds(z, x, y):
    """(west, south, east, north) in degrees of one XYZ tile."""
    n = 2 ** z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west, south, east, north


_transparent = None


def transparent_tile():
    """PNG bytes of a fully transparent 256x256 tile, encoded once."""
    global _transparent
    if _transparent is None:
        _transparent = rasters.encode_png(np.zeros((gridmath.TILE_SIZE, gridmath.TILE_SIZE, 4), dtype=np.uint8))
    return _transparent


def _touches(corners, z, x, y):
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    west, south, east, north = tile_lonlat_bounds(z, x, y)
    return min(lons) <= east and west <= max(lons) and min(lats) <= north and south <= max(lats)


def render_tile(scene_id, corners, z, x, y):
    """One tile of the scene as PNG bytes, from the disk cache when present."""
    if not _touches(corners, z, x, y):
        return transparent_tile()
    target = paths.TILE_CACHE / f"emit-{scene_id}" / str(z) / str(x) / f"{y}.png"
    if target.is_file():
        return target.read_bytes()
    out = gdal.Warp(
        "", gcp_source(scene_id, corners),
        format="MEM", dstSRS="EPSG:3857", outputBounds=gridmath.tile_bounds(z, x, y),
        width=gridmath.TILE_SIZE, height=gridmath.TILE_SIZE,
        resampleAlg="bilinear", polynomialOrder=1, dstAlpha=True, multithread=True,
    )
    rgba = np.transpose(out.ReadAsArray(), (1, 2, 0))
    blob = rasters.encode_png(np.ascontiguousarray(rgba))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{threading.get_ident()}.part")
    tmp.write_bytes(blob)
    tmp.replace(target)
    return blob
