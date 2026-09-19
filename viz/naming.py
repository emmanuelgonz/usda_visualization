"""CPC filename grammar, CDL crop code sets, and CPC-to-CDL year pairing."""

import re

CROPS = ("corn", "cotton", "soy", "wheat")
VARS = ("cond", "prog")

VAR_LABELS = {"cond": "Condition", "prog": "Progress"}

# CDL class codes per CPC crop. A double-crop class counts toward both of its
# constituent crops, so codes such as 26 and 241 appear under two crops.
# Sweet Corn (12), Pop or Orn Corn (13), and Buckwheat (39) are excluded:
# NASS does not survey them under these progress and condition series.
CROP_CODES = {
    "corn": {"primary": (1,), "double": (225, 226, 228, 237, 241)},
    "cotton": {"primary": (2,), "double": (232, 238, 239)},
    "soy": {"primary": (5,), "double": (26, 239, 240, 241, 254)},
    "wheat": {"primary": (22, 23, 24), "double": (26, 225, 238)},
}

# The crop prefix is matched case-insensitively on purpose. USDA changed the
# convention partway through the archive: 2015-2020 name corn and soy files
# CornCond24w15.tif and SoyCond24w15.tif, while 2021 onward use cornCond24w15.tif.
# A lowercase-only pattern silently drops 590 rasters across those six years.
_CPC_RE = re.compile(
    r"^(?P<crop>[A-Za-z]+)(?P<var>Cond|Prog)(?P<yy>\d{2})w(?P<ww>\d{1,2})\.tif$"
)


def parse_cpc_filename(name):
    """Return crop/var/year/week for a CPC raster name, or None if it is not one."""
    match = _CPC_RE.match(name)
    if match is None:
        return None
    crop = match.group("crop").lower()
    if crop not in CROPS:
        return None
    return {
        "crop": crop,
        "var": match.group("var").lower(),
        "year": 2000 + int(match.group("yy")),
        "week": int(match.group("ww")),
    }


def cpc_filename(crop, var, year, week):
    """Build the CPC raster name for one crop, variable, year, and week."""
    return f"{crop}{var.capitalize()}{year % 100:02d}w{week:02d}.tif"


def cpc_relpath(crop, var, year, week):
    """Path of a CPC raster relative to the CPC data directory."""
    return f"{crop}/{var}/{cpc_filename(crop, var, year, week)}"


def pair_cdl_year(cpc_year, cdl_years):
    """Pick the CDL year to show beneath a CPC year, clamping to available coverage."""
    years = sorted(cdl_years)
    if not years:
        raise ValueError("no CDL years available")
    if cpc_year in years:
        return cpc_year
    if cpc_year < years[0]:
        return years[0]
    if cpc_year > years[-1]:
        return years[-1]
    return min(years, key=lambda y: (abs(y - cpc_year), y))
