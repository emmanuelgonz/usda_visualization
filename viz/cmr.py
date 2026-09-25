"""Shared access to NASA's Common Metadata Repository granule search.

Used by the EMIT, ECOSTRESS, and HLS fetch scripts. These are the project's
only network steps; the interface never touches the network.
"""

import http.client
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
BBOX = "-125,24.4,-66.9,49.4"
PAGE_SIZE = 2000


def page_url(short_name, page_num, version=None, temporal=None):
    """One page of a granule search over the CONUS box."""
    parts = [f"short_name={short_name}"]
    if version:
        parts.append(f"version={version}")
    parts.append(f"bounding_box={BBOX}")
    if temporal:
        parts.append(f"temporal={temporal}")
    parts.append(f"page_size={PAGE_SIZE}")
    parts.append(f"page_num={page_num}")
    return CMR_URL + "?" + "&".join(parts)


def _retrying(url, read):
    """read(response) for one URL, retried three times on transient failures.

    Covers connection errors, HTTP errors, a body cut short (IncompleteRead is
    an HTTPException, not an OSError), and a malformed page.
    """
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return read(response)
        except (OSError, http.client.HTTPException, ValueError, KeyError) as exc:
            if attempt == 2:
                raise
            print(f"{url[-40:]}: {exc}; retrying", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def fetch_page(url):
    """Entries from one CMR page. Retries transient failures three times."""
    return _retrying(url, lambda response: json.load(response)["feed"]["entry"])


def fetch_response(url):
    """(body bytes, response headers) from one URL. Retries transient failures three times."""
    return _retrying(url, lambda response: (response.read(), response.headers))


def polygon_ring(entry):
    """The entry's first polygon as a closed [lon, lat] ring, or None.

    CMR lists latitude then longitude; GeoJSON wants longitude then latitude.
    """
    polygons = entry.get("polygons")
    if not polygons or not polygons[0]:
        return None
    numbers = [float(v) for v in polygons[0][0].split()]
    ring = [[numbers[i + 1], numbers[i]] for i in range(0, len(numbers), 2)]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def fetch_all(fetch_page_fn):
    """Every entry, paging until a page comes back empty."""
    entries = []
    page = 1
    while True:
        got = fetch_page_fn(page)
        if not got:
            return entries
        entries.extend(got)
        print(f"page {page}: {len(got)} granules (total {len(entries)})", flush=True)
        page += 1


def link(entry, suffix):
    """First http(s) href whose rel ends with suffix, or None."""
    for item in entry.get("links", []):
        href = item.get("href", "")
        if item.get("rel", "").endswith(suffix) and href.startswith("http"):
            return href
    return None


def write_geojson(features, path):
    """Write a FeatureCollection via a .part file and an atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    os.replace(part, path)
