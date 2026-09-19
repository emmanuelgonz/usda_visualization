"""Local tile server: routes, on-demand warping, and the disk tile cache."""

import argparse
import json
import re
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

from viz import color, naming, paths, rasters

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}

TILE_CDL_RE = re.compile(r"^/tiles/cdl/(?P<year>\d{4})/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$")
TILE_CPC_RE = re.compile(
    r"^/tiles/cpc/(?P<crop>[a-z]+)/(?P<var>cond|prog)/(?P<year>\d{4})/(?P<week>\d+)"
    r"/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$"
)

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


def render_cdl_tile(year, z, x, y):
    """One CDL tile: nearest-neighbour warp, then the source palette."""
    def render():
        codes = rasters.warp_tile(str(cdl_path(year)), z, x, y, resample="near")
        return rasters.encode_png(color.colorize_thematic(codes, cdl_palette(year)))

    return _cached(f"cdl-{year}", z, x, y, render)


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


def point_report(lon, lat, crop, year, cdl_year):
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
        for week in weeks:
            path = cpc_path(crop, var, year, week)
            if not path.is_file():
                continue
            points.append({"week": week, "value": rasters.sample_point(str(path), x5070, y5070)[0]})
        series[var] = points

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
                return self._send(paths.CATALOG.read_bytes(), CONTENT_TYPES[".json"])

            if route == "/api/point":
                return self._handle_point(query)

            match = TILE_CDL_RE.match(route)
            if match:
                return self._handle_cdl_tile(match)

            match = TILE_CPC_RE.match(route)
            if match:
                return self._handle_cpc_tile(match, query)

            return self._fail(HTTPStatus.NOT_FOUND, "not found")
        except BrokenPipeError:
            pass
        except Exception as exc:  # a bad tile must not take the server down
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def _handle_cdl_tile(self, match):
        year = int(match.group("year"))
        if not cdl_path(year).is_file():
            return self._fail(HTTPStatus.NOT_FOUND, f"no CDL raster for {year}")
        z, x, y = (int(match.group(k)) for k in ("z", "x", "y"))
        self._send(render_cdl_tile(year, z, x, y), "image/png", cache=True)

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

    def _handle_point(self, query):
        required = ("lon", "lat", "crop", "year", "cdl_year")
        if any(key not in query for key in required):
            return self._fail(HTTPStatus.BAD_REQUEST, f"required: {', '.join(required)}")
        try:
            lon = float(query["lon"][0])
            lat = float(query["lat"][0])
            year = int(query["year"][0])
            cdl_year = int(query["cdl_year"][0])
        except ValueError:
            return self._fail(HTTPStatus.BAD_REQUEST,
                               "lon, lat, year, cdl_year must be numeric")
        report = point_report(lon, lat, query["crop"][0], year, cdl_year)
        self._send(json.dumps(report).encode(), CONTENT_TYPES[".json"])


def make_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the CPC-over-CDL map locally.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

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
