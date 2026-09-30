"""Local tile server: routes, on-demand warping, and the disk tile cache."""

import argparse
import datetime
import http.client
import json
import math
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

from viz import basins, catalog, coincidence, color, eco_browse, emit, filters, naming, paths, rasters, registry
from viz.archetypes import tiled

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".geojson": "application/geo+json",
}

TILE_CDL_RE = re.compile(r"^/tiles/cdl/(?P<year>\d{4})/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$")
TILE_CPC_RE = re.compile(
    r"^/tiles/cpc/(?P<crop>[a-z]+)/(?P<var>cond|prog)/(?P<year>\d{4})/(?P<week>\d+)"
    r"/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$"
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CATALOG_BUSY = "catalog busy; a refresh is in progress, retry shortly"
NO_CATALOG = "no catalog; run ./run.sh migrate once, then ./run.sh refresh"
REGISTRY = None      # loaded by load_registry() at start; tests call it too


def load_registry(path=None):
    global REGISTRY
    REGISTRY = registry.load(path or paths.MISSIONS)
    return REGISTRY


def current_registry():
    return REGISTRY if REGISTRY is not None else load_registry()


def catalog_path():
    return paths.catalog_db(current_registry().region.name)


def catalog_read(work=None):
    """(value, busy) for one read of this thread's catalog, degraded instead of raised.

    work(entry) runs against the (Catalog, {grid: RingIndex}) pair. value is
    None when the catalog is absent or locked; busy tells those apart. Any
    OperationalError that is not a lock is a fault and propagates to the 500 handler.
    """
    try:
        entry = catalog.catalog_for(catalog_path())
        if entry is None:
            return None, False
        return (entry if work is None else work(entry)), False
    except catalog.BUSY as exc:
        if not catalog.is_busy(exc):
            raise
        return None, True

# Changes on every server start. The interface appends it to tile URLs so the
# browser's HTTP cache is invalidated whenever the server (and so any palette
# or ramp code) is restarted, while tiles stay cached within one run.
SERVER_TOKEN = str(int(time.time()))

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
    tmp = target.with_name(f"{target.name}.{threading.get_ident()}.part")
    tmp.write_bytes(blob)
    tmp.replace(target)
    return blob


def focus_palette(year, crop, style):
    """The CDL palette with every class but this crop's dimmed, cached per year, crop and style."""
    key = f"{year}-f{crop}-{style}"
    with _palette_lock:
        lut = _palettes.get(key)
    if lut is None:
        codes = naming.CROP_CODES[crop]
        lut = color.focus_lut(cdl_palette(year), tuple(codes["primary"]) + tuple(codes["double"]), style)
        with _palette_lock:
            _palettes[key] = lut
    return lut


def render_cdl_tile(year, z, x, y, focus=None, style="white"):
    """One CDL tile: nearest-neighbour warp, then the source or focused palette."""
    key = f"cdl-{year}" + (f"-f{focus}-{style}" if focus else "")

    def render():
        codes = rasters.warp_tile(str(cdl_path(year)), z, x, y, resample="near")
        lut = focus_palette(year, focus, style) if focus else cdl_palette(year)
        return rasters.encode_png(color.colorize_thematic(codes, lut))

    return _cached(key, z, x, y, render)


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


def tiled_block(cat, indexes, mission, lon, lat, granules, start, end, values, window):
    """The point's tiles over [start, end] for a tiled mission, and a nearest-clear tag on each EMIT granule.

    granules are copies, never the index's own dicts. A granule's "hls" key is
    absent when no tile covers the point, None when no clear acquisition lies
    within the window. start and end are None without a range or week, and
    every tile then lists no acquisitions.
    """
    index = indexes.get(mission.grid)
    tiles = index.covering(lon, lat) if index else []
    block = {"start": start, "end": end, "cloud": values["cloud"], "sensor": values["sensor"], "window": window, "tiles": []}
    if not tiles:
        return block
    rows = tiled.acquisitions(cat, mission, tiles, start, end) if start and end else []
    for tile in tiles:
        acq = [{"date": r["date"], "time": r["time"], "sensor": r["sensor"], "cloud": r["cloud"]} for r in rows if r["tile"] == tile]
        clear = sum(1 for r in acq if filters.passes(r, mission, values))
        block["tiles"].append({"tile": tile, "clear": clear, "acq": acq})
    dated = [g for g in granules if g.get("start")]
    clear_rows = []
    if dated:
        first = min(g["start"][:10] for g in dated)
        last = max(g["start"][:10] for g in dated)
        lo = (datetime.date.fromisoformat(first) - datetime.timedelta(days=window)).isoformat()
        hi = (datetime.date.fromisoformat(last) + datetime.timedelta(days=window)).isoformat()
        clear_rows = [r for r in tiled.acquisitions(cat, mission, tiles, lo, hi) if filters.passes(r, mission, values)]
    for g in granules:
        g["hls"] = tiled.nearest_clear(clear_rows, g["start"][:10], window) if g.get("start") else None
    return block


def basin_units(lon, lat):
    """{'huc2': {huc, name} | None, 'huc4': ...} from the vendored hydrologic units, or None when neither file exists."""
    indexes = {level: basins.index_for(path) for level, path in (("huc2", paths.BASINS2), ("huc4", paths.BASINS4))}
    if all(index is None for index in indexes.values()):
        return None
    return {level: index.covering(lon, lat) if index is not None else None for level, index in indexes.items()}


def point_report(lon, lat, crop, year, cdl_year, week=None, hls_params=None):
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
        for cpc_week in weeks:
            path = cpc_path(crop, var, year, cpc_week)
            if not path.is_file():
                continue
            points.append({"week": cpc_week, "value": rasters.sample_point(str(path), x5070, y5070)[0]})
        series[var] = points

    index = emit.index_for(paths.EMIT_FOOTPRINTS)
    # Copies, so tagging never touches the shared index; hls_near is the map-only pairing list.
    granules = [{k: v for k, v in g.items() if k != "hls_near"} for g in index.covering(lon, lat)] if index else []
    eco_index = emit.index_for(paths.ECO_FOOTPRINTS)
    eco_swaths = eco_index.covering(lon, lat) if eco_index else []
    sunday = None
    if week:
        try:
            sunday = emit.week_sunday(year, week).isoformat()
        except ValueError:
            sunday = None

    params = dict(hls_params or {})
    if not (params.get("start") and params.get("end")):
        if sunday:
            centre = datetime.date.fromisoformat(sunday)
            span = datetime.timedelta(days=params.get("window", 7))
            params["start"] = (centre - span).isoformat()
            params["end"] = (centre + span).isoformat()
        else:
            params["start"] = params["end"] = None   # no range to list over; the tags still run
    hls_mission = current_registry().missions.get("hls")
    values = {"cloud": params.get("cloud", 30), "sensor": params.get("sensor", "ALL")}
    missions_report, _ = catalog_read(lambda entry: {"hls": tiled_block(
        entry[0], entry[1], hls_mission, lon, lat, granules, params["start"], params["end"], values, params.get("window", 7))}) \
        if hls_mission else (None, False)

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
        "emit": granules,
        "eco": eco_swaths,
        "week_sunday": sunday,
        "basins": basin_units(lon, lat),
        "missions": missions_report,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "usda-viz/1.0"
    protocol_version = "HTTP/1.1"

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
                try:
                    target.relative_to(paths.WEB.resolve())
                except ValueError:
                    return self._fail(HTTPStatus.FORBIDDEN, "forbidden")
                return self._serve_file(target)

            if route == "/api/catalog":
                if not paths.CATALOG.is_file():
                    return self._fail(HTTPStatus.NOT_FOUND, "catalog not built; run prepare")
                catalog = json.loads(paths.CATALOG.read_text())
                catalog["server_token"] = SERVER_TOKEN
                index = emit.index_for(paths.EMIT_FOOTPRINTS)
                catalog["emit_count"] = index.count if index else 0
                catalog["emit_fetched"] = index.fetched if index else None
                eco_index = emit.index_for(paths.ECO_FOOTPRINTS)
                catalog["eco_count"] = eco_index.count if eco_index else 0
                catalog["eco_fetched"] = eco_index.fetched if eco_index else None
                catalog["emit_hls_paired"] = index.count_where(lambda p: bool(p.get("hls_near"))) if index else 0
                catalog["coincident_15min"] = index.count_where(
                    lambda p: bool(p.get("eco")) and abs(p["eco"][0]["dt"]) <= coincidence.SAME_PASS
                ) if index else 0
                def mission_rows(entry):
                    cat, indexes = entry
                    rows = []
                    for m in current_registry().missions.values():
                        summary = cat.summary(m.key)
                        rows.append({"key": m.key, "name": m.name, "label": m.label, "archetype": m.archetype,
                                     "grid": m.grid, "filters": filters.describe(m), "style": m.style,
                                     "count": summary["count"], "fetched": summary["fetched"],
                                     "tiles": indexes[m.grid].count if m.grid and m.grid in indexes else 0})
                    return rows
                missions, busy = catalog_read(mission_rows)
                if missions is None:
                    missions = [{"key": m.key, "name": m.name, "label": m.label, "archetype": m.archetype, "grid": m.grid,
                                 "filters": filters.describe(m), "style": m.style, "count": 0, "fetched": None, "tiles": 0}
                                for m in current_registry().missions.values()]
                catalog["missions"] = missions
                catalog["catalog_busy"] = busy
                return self._send(json.dumps(catalog).encode(), CONTENT_TYPES[".json"])

            if route == "/api/emit/footprints.geojson":
                if not paths.EMIT_FOOTPRINTS.is_file():
                    return self._fail(HTTPStatus.NOT_FOUND, "no EMIT footprints; run ./run.sh footprints")
                return self._send(paths.EMIT_FOOTPRINTS.read_bytes(), CONTENT_TYPES[".geojson"])

            if route == "/api/eco/footprints.geojson":
                if not paths.ECO_FOOTPRINTS.is_file():
                    return self._fail(HTTPStatus.NOT_FOUND, "no ECOSTRESS footprints; run ./run.sh footprints")
                return self._send(paths.ECO_FOOTPRINTS.read_bytes(), CONTENT_TYPES[".geojson"])

            if route == "/api/eco/browse":
                return self._handle_eco_browse(query)

            match = re.match(r"^/api/missions/([a-z][a-z0-9_]*)/(tiles\.geojson|counts)$", route)
            if match:
                return self._handle_mission(match.group(1), match.group(2), query)

            if route == "/api/point":
                return self._handle_point(query)

            match = TILE_CDL_RE.match(route)
            if match:
                return self._handle_cdl_tile(match, query)

            match = TILE_CPC_RE.match(route)
            if match:
                return self._handle_cpc_tile(match, query)

            return self._fail(HTTPStatus.NOT_FOUND, "not found")
        except BrokenPipeError:
            pass
        except Exception as exc:  # a bad tile must not take the server down
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def _handle_cdl_tile(self, match, query):
        year = int(match.group("year"))
        if not cdl_path(year).is_file():
            return self._fail(HTTPStatus.NOT_FOUND, f"no CDL raster for {year}")
        focus = query["focus"][0] if "focus" in query else None
        if focus is not None and focus not in naming.CROPS:
            return self._fail(HTTPStatus.NOT_FOUND, f"unknown crop {focus}")
        style = query["dim"][0] if "dim" in query else "white"
        if style not in color.FOCUS_STYLES:
            return self._fail(HTTPStatus.NOT_FOUND, f"unknown dim style {style}")
        z, x, y = (int(match.group(k)) for k in ("z", "x", "y"))
        self._send(render_cdl_tile(year, z, x, y, focus, style), "image/png", cache=True)

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

    def _range_params(self, query, required):
        """Validated range parameters, or (None, message). Missing optional ones take defaults."""
        params = {}
        given = [key for key in ("start", "end") if key in query]
        if required and len(given) < 2:
            return None, "required: start, end"
        if len(given) == 1:
            return None, "start and end go together"
        for key in given:
            value = query[key][0]
            if not DATE_RE.match(value):
                return None, f"{key} must be YYYY-MM-DD"
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                return None, f"{key} is not a calendar date"
            params[key] = value
        if given and params["start"] > params["end"]:
            return None, "start must not be after end"
        try:
            params["cloud"] = int(query["cloud"][0]) if "cloud" in query else 30
            params["window"] = int(query["window"][0]) if "window" in query else 7
        except ValueError:
            return None, "cloud and window must be integers"
        if not (0 <= params["cloud"] <= 100) or not (0 <= params["window"] <= 60):
            return None, "cloud must be 0-100 and window 0-60"
        params["sensor"] = query["sensor"][0] if "sensor" in query else "ALL"
        if params["sensor"] not in ("ALL", "L30", "S30"):   # the point route only, until the readout generalizes
            return None, "sensor must be ALL, L30, or S30"
        return params, None

    def _handle_mission(self, key, what, query):
        mission = current_registry().missions.get(key)
        if mission is None:
            return self._fail(HTTPStatus.NOT_FOUND, "unknown mission")
        if mission.archetype != "tiled":
            return self._fail(HTTPStatus.NOT_FOUND, f"mission {key} is not tiled")
        entry, busy = catalog_read()
        if busy:
            return self._fail(HTTPStatus.SERVICE_UNAVAILABLE, CATALOG_BUSY)
        if entry is None:
            return self._fail(HTTPStatus.NOT_FOUND, NO_CATALOG)
        if what == "tiles.geojson":
            geo, busy = catalog_read(lambda opened: self._tiles_bytes(opened[0], mission.grid))
            if busy:
                return self._fail(HTTPStatus.SERVICE_UNAVAILABLE, CATALOG_BUSY)
            return self._send(geo or b'{"type": "FeatureCollection", "features": []}', CONTENT_TYPES[".geojson"])
        params, message = self._range_params(query, required=True)
        if params is None:
            return self._fail(HTTPStatus.BAD_REQUEST, message)
        try:
            values = filters.parse(mission, query)
        except filters.FilterError as exc:
            return self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        counts, busy = catalog_read(lambda opened: tiled.counts(opened[0], mission, params["start"], params["end"], values))
        if busy:
            return self._fail(HTTPStatus.SERVICE_UNAVAILABLE, CATALOG_BUSY)
        self._send(json.dumps({"counts": counts or {}}).encode(), CONTENT_TYPES[".json"])

    @staticmethod
    def _tiles_bytes(cat, grid):
        cache = getattr(cat, "_tiles_bytes", None)
        if cache is None:
            cache = cat._tiles_bytes = {}
        if grid not in cache:
            cache[grid] = json.dumps(tiled.tiles_geojson(cat, grid)).encode()
        return cache[grid]

    def _handle_eco_browse(self, query):
        try:
            swath_id = query["id"][0]
            lon = float(query["lon"][0])
            lat = float(query["lat"][0])
        except (KeyError, ValueError):
            return self._fail(HTTPStatus.BAD_REQUEST, "required: id, lon, lat (numbers)")
        if not (math.isfinite(lon) and math.isfinite(lat)
                and -180 <= lon <= 180 and -90 <= lat <= 90):
            return self._fail(HTTPStatus.BAD_REQUEST, "lon/lat out of range")
        try:
            eco_browse.parse_swath_id(swath_id)
        except ValueError as exc:
            return self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        try:
            found = eco_browse.lookup(swath_id, lon, lat)
        except (OSError, http.client.HTTPException, ValueError, KeyError, IndexError):
            return self._fail(HTTPStatus.BAD_GATEWAY, "CMR lookup failed")
        if found is None:
            return self._fail(HTTPStatus.NOT_FOUND,
                              "no tiled ECOSTRESS image covers this point for that swath")
        self._send(json.dumps(found).encode(), CONTENT_TYPES[".json"])

    def _handle_point(self, query):
        required = ("lon", "lat", "crop", "year", "cdl_year")
        if any(key not in query for key in required):
            return self._fail(HTTPStatus.BAD_REQUEST, f"required: {', '.join(required)}")
        try:
            lon = float(query["lon"][0])
            lat = float(query["lat"][0])
            year = int(query["year"][0])
            cdl_year = int(query["cdl_year"][0])
            week = int(query["week"][0]) if "week" in query else None
        except ValueError:
            return self._fail(HTTPStatus.BAD_REQUEST,
                               "lon, lat, year, cdl_year must be numeric")
        if week is not None and not (1 <= week <= 53):
            return self._fail(HTTPStatus.BAD_REQUEST, "week must be 1-53")
        hls_params, message = self._range_params(query, required=False)
        if hls_params is None:
            return self._fail(HTTPStatus.BAD_REQUEST, message)
        report = point_report(lon, lat, query["crop"][0], year, cdl_year, week, hls_params=hls_params)
        self._send(json.dumps(report).encode(), CONTENT_TYPES[".json"])


def make_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the CPC-over-CDL map locally.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    try:
        load_registry()
    except registry.RegistryError as exc:
        print(f"registry: {exc}", file=sys.stderr)
        return 2

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
