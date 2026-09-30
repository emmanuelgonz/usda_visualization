"""Fixed tile grids for tiled missions, one module each exposing ring(tile_id)."""

from viz.grids import mgrs

NAMES = ("mgrs",)
_MODULES = {"mgrs": mgrs}


def get(name):
    """The grid module for a registry grid name; KeyError for an unknown one."""
    return _MODULES[name]
