"""The mission registry: viz/missions.json loaded and validated once.

Each mission names its CMR collections, its archetype (swath scenes with a
footprint each, or tiled acquisitions on a fixed grid), the attributes to keep
from CMR, the filters the sidebar offers, how to find a browse picture, and how
to draw it. Validation fails with the mission key in the message, so a bad
entry is caught at start rather than at the first fetched row.
"""

import json
import re
from dataclasses import dataclass, field

from viz import paths

ARCHETYPES = ("swath", "tiled")
FOOTPRINTS = ("polygon", "box")
GRIDS = ("mgrs",)
CONTROLS = ("max", "choice")
BROWSE_SOURCES = ("links", "sibling")
ATTRIBUTE_TYPES = ("number", "int", "text")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class RegistryError(ValueError):
    """The registry file is malformed; the message names the mission."""


@dataclass(frozen=True)
class Region:
    name: str
    bbox: tuple


@dataclass(frozen=True)
class Collection:
    short_name: str
    version: str
    implies: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Attribute:
    source: str
    type: str


@dataclass(frozen=True)
class Filter:
    attribute: str
    control: str
    default: object
    label: str
    values: tuple = ()


@dataclass(frozen=True)
class TileFrom:
    field: str
    pattern: re.Pattern


@dataclass(frozen=True)
class Mission:
    key: str
    name: str
    label: str
    archetype: str
    cmr: tuple
    since: str
    attributes: dict
    filters: tuple
    browse: dict
    links: dict
    style: dict
    footprint: str = None
    grid: str = None
    tile_from: TileFrom = None


class Registry:
    def __init__(self, region, missions):
        self.region = region
        self.missions = {m.key: m for m in missions}

    def mission(self, key):
        return self.missions[key]

    def tiled(self):
        return [m for m in self.missions.values() if m.archetype == "tiled"]

    def swath(self):
        return [m for m in self.missions.values() if m.archetype == "swath"]


def _fail(key, message):
    raise RegistryError(f"mission {key!r}: {message}")


def _compile(key, what, pattern, groups=0):
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        _fail(key, f"{what} pattern does not compile: {exc}")
    if compiled.groups < groups:
        _fail(key, f"{what} pattern needs {groups} capture group(s)")
    return compiled


def _mission(entry):
    key = entry.get("key")
    if not key or not re.match(r"^[a-z][a-z0-9_]*$", key):
        raise RegistryError(f"mission key {key!r} must be lower-case letters, digits, and underscores")
    archetype = entry.get("archetype")
    if archetype not in ARCHETYPES:
        _fail(key, f"unknown archetype {archetype!r}")
    since = entry.get("since", "")
    if not _MONTH_RE.match(since):
        _fail(key, f"since must be YYYY-MM, got {since!r}")
    collections = tuple(Collection(c["short_name"], str(c["version"]), dict(c.get("implies", {})))
                        for c in entry.get("cmr", []))
    if not collections:
        _fail(key, "needs at least one cmr collection")
    attributes = {}
    for name, spec in entry.get("attributes", {}).items():
        if spec.get("type") not in ATTRIBUTE_TYPES:
            _fail(key, f"attribute {name!r} has unknown type {spec.get('type')!r}")
        attributes[name] = Attribute(spec["from"], spec["type"])
    filters = []
    for spec in entry.get("filters", []):
        if spec.get("attribute") not in attributes:
            _fail(key, f"filter on undeclared attribute {spec.get('attribute')!r}")
        if spec.get("control") not in CONTROLS:
            _fail(key, f"unknown filter control {spec.get('control')!r}")
        values = tuple(spec.get("values", ()))
        if spec["control"] == "choice" and spec.get("default") not in values:
            _fail(key, f"choice default {spec.get('default')!r} is not one of {values}")
        filters.append(Filter(spec["attribute"], spec["control"], spec.get("default"), spec.get("label", ""), values))
    browse = dict(entry.get("browse", {}))
    if browse.get("source") not in BROWSE_SOURCES:
        _fail(key, f"unknown browse source {browse.get('source')!r}")
    if browse["source"] == "links":
        _compile(key, "browse match", browse.get("match", ""))
    else:
        for name in ("short_name", "version", "id_pattern", "sibling_pattern", "kinds"):
            if name not in browse:
                _fail(key, f"sibling browse needs {name!r}")
        _compile(key, "browse id", browse["id_pattern"])
    for name, spec in entry.get("links", {}).items():
        _compile(key, f"link {name!r} match", spec.get("match", ""))
    footprint = grid = tile_from = None
    if archetype == "swath":
        footprint = entry.get("footprint")
        if footprint not in FOOTPRINTS:
            _fail(key, f"swath needs a footprint of {FOOTPRINTS}, got {footprint!r}")
    else:
        grid = entry.get("grid")
        if grid not in GRIDS:
            _fail(key, f"unknown grid {grid!r}")
        spec = entry.get("tile_from")
        if not spec or "field" not in spec or "pattern" not in spec:
            _fail(key, "tiled needs tile_from with field and pattern")
        tile_from = TileFrom(spec["field"], _compile(key, "tile_from", spec["pattern"], groups=1))
    return Mission(key, entry.get("name", key), entry.get("label", key), archetype, collections, since,
                   attributes, tuple(filters), browse, dict(entry.get("links", {})), dict(entry.get("style", {})),
                   footprint, grid, tile_from)


def parse(data):
    """A Registry from the decoded file; RegistryError on any problem."""
    region = data.get("region") or {}
    bbox = region.get("bbox")
    if not region.get("name") or not isinstance(bbox, list) or len(bbox) != 4:
        raise RegistryError("region needs a name and a four-number bbox")
    missions = []
    seen = set()
    for entry in data.get("missions", []):
        mission = _mission(entry)
        if mission.key in seen:
            _fail(mission.key, "duplicate key")
        seen.add(mission.key)
        missions.append(mission)
    return Registry(Region(region["name"], tuple(float(v) for v in bbox)), missions)


def load(path=paths.MISSIONS):
    """The registry at path (default viz/missions.json)."""
    with open(path, encoding="utf-8") as handle:
        return parse(json.load(handle))
