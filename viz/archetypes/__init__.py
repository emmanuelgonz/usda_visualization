"""The two mission archetypes: swath scenes with a footprint each, and tiled acquisitions on a grid.

Each module has the same fetch-side duties: build a month's request, parse
a page into catalog rows, and fetch a whole month. The serving duties arrive
as the missions migrate.
"""

import importlib

NAMES = ("swath", "tiled")


def get(name):
    """The archetype module for a registry archetype name; KeyError for an unknown one."""
    if name not in NAMES:
        raise KeyError(name)
    return importlib.import_module(f"viz.archetypes.{name}")
