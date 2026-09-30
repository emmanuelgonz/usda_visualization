"""Filter values for a mission's controls, parsed from a query string and applied to rows.

A registry `max` control takes an integer 0 to 100 (the cloud threshold); a
`choice` control takes one of its values, with ALL meaning no filter.
"""


class FilterError(ValueError):
    """A bad or unknown filter parameter; the message names it."""


def parse(mission, query, prefix="f."):
    """{attribute: value} for the mission's filters from a parse_qs dict; defaults when absent."""
    known = {f.attribute: f for f in mission.filters}
    values = {name: f.default for name, f in known.items()}
    for key, raw in query.items():
        if not key.startswith(prefix):
            continue
        name = key[len(prefix):]
        if name not in known:
            raise FilterError(f"unknown filter {name!r}")
        control = known[name]
        text = raw[0] if isinstance(raw, list) else raw
        if control.control == "max":
            try:
                number = int(text)
            except ValueError:
                raise FilterError(f"{name} must be an integer") from None
            if not 0 <= number <= 100:
                raise FilterError(f"{name} must be 0-100")
            values[name] = number
        else:
            if text not in control.values:
                raise FilterError(f"{name} must be one of {', '.join(control.values)}")
            values[name] = text
    return values


def passes(row, mission, values):
    """True when the row satisfies every filter value."""
    for control in mission.filters:
        value = values.get(control.attribute, control.default)
        have = row.get(control.attribute)
        if control.control == "max":
            if have is None or have > value:
                return False
        elif value != "ALL" and have != value:
            return False
    return True


def describe(mission):
    """JSON-able descriptions of the mission's filters for the catalog route."""
    return [{"attribute": f.attribute, "control": f.control, "default": f.default, "label": f.label,
             "values": list(f.values)} for f in mission.filters]
