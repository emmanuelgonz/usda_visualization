"""Lookup tables mapping raster values to RGBA.

Both CPC domains are fixed rather than stretched per frame, so a color carries
the same meaning in every week, crop, and year and animation stays comparable.
"""

import numpy as np

NODATA = -9999.0

COND_DOMAIN = (1.0, 5.0)
PROG_DOMAIN = (0.0, 1.0)

DOMAINS = {"cond": COND_DOMAIN, "prog": PROG_DOMAIN}

# Control points as (position in 0-1, (r, g, b)).
RAMPS = {
    # Diverging: very poor through fair at the midpoint to excellent.
    "cond": [
        (0.00, (165, 15, 21)),
        (0.25, (222, 92, 55)),
        (0.50, (247, 224, 143)),
        (0.75, (120, 183, 92)),
        (1.00, (26, 110, 47)),
    ],
    # Sequential: unplanted through fully progressed.
    "prog": [
        (0.00, (247, 244, 233)),
        (0.33, (196, 214, 172)),
        (0.66, (110, 166, 148)),
        (1.00, (25, 74, 110)),
    ],
}

COND_LABELS = ["Very poor", "Poor", "Fair", "Good", "Excellent"]


def ramp_lut(var):
    """Build a 256-entry RGBA lookup table for one continuous variable."""
    stops = RAMPS[var]
    positions = np.array([p for p, _ in stops], dtype=np.float64)
    colors = np.array([c for _, c in stops], dtype=np.float64)
    t = np.linspace(0.0, 1.0, 256)
    lut = np.empty((256, 4), dtype=np.uint8)
    for channel in range(3):
        lut[:, channel] = np.interp(t, positions, colors[:, channel]).round().astype(np.uint8)
    lut[:, 3] = 255
    return lut


def palette_lut(color_table):
    """Build a 256-entry RGBA lookup table from a GDAL color table.

    Class 0 is Background and always renders transparent, as do any codes the
    table does not define.
    """
    lut = np.zeros((256, 4), dtype=np.uint8)
    if color_table is None:
        return lut
    for index in range(256):
        entry = color_table.GetColorEntry(index)
        if entry is None:
            continue
        rgba = tuple(entry) + (255,) * (4 - len(entry))
        lut[index] = rgba[:4]
    lut[0] = (0, 0, 0, 0)
    return lut


def colorize_continuous(values, var, alpha=None):
    """Map a float array to RGBA through the fixed ramp for that variable.

    NaN and the CPC NoData sentinel render fully transparent. When alpha is
    given (a 0-1 array of the same shape, typically a crop fraction) it scales
    opacity multiplicatively.
    """
    lo, hi = DOMAINS[var]
    data = np.asarray(values, dtype=np.float32)
    invalid = ~np.isfinite(data) | (data == NODATA)

    scaled = (np.nan_to_num(data, nan=lo, posinf=hi, neginf=lo) - lo) / (hi - lo)
    indices = np.clip(scaled * 255.0, 0.0, 255.0).astype(np.uint8)

    out = ramp_lut(var)[indices]
    if alpha is not None:
        weight = np.clip(np.asarray(alpha, dtype=np.float32), 0.0, 1.0)
        out[..., 3] = (out[..., 3] * weight).round().astype(np.uint8)
    out[invalid, 3] = 0
    return out


def colorize_thematic(codes, lut):
    """Map a byte class array to RGBA through a palette lookup table."""
    return lut[np.asarray(codes, dtype=np.uint8)]


def legend_stops(var):
    """Legend entries for one variable: value, label, and CSS color."""
    lo, hi = DOMAINS[var]
    lut = ramp_lut(var)

    def css(value):
        index = int(round(np.clip((value - lo) / (hi - lo), 0.0, 1.0) * 255))
        r, g, b = lut[index, :3]
        return f"#{r:02x}{g:02x}{b:02x}"

    if var == "cond":
        return [
            {"value": value, "label": label, "color": css(value)}
            for value, label in zip((1.0, 2.0, 3.0, 4.0, 5.0), COND_LABELS)
        ]
    return [
        {"value": value, "label": f"{int(value * 100)}%", "color": css(value)}
        for value in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]


def focus_lut(base_lut, keep_codes):
    """Copy a palette, greying every class except keep_codes and class 0.

    Grey is the Rec. 601 luminance of the original colour, so water stays
    dark and developed land stays light while only the kept classes carry
    hue. Alpha is preserved; class 0 stays transparent.
    """
    out = np.array(base_lut, dtype=np.uint8, copy=True)
    keep = np.zeros(256, dtype=bool)
    keep[list(keep_codes)] = True
    keep[0] = True
    rgb = out[:, :3].astype(np.float32)
    luma = (0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]).round().astype(np.uint8)
    grey = ~keep
    out[grey, 0] = luma[grey]
    out[grey, 1] = luma[grey]
    out[grey, 2] = luma[grey]
    return out
