"""Resolve the request's start/finish locally (no external call).

Accepted formats:
    "Paris, TX"         city + two-letter state
    "Paris, Texas"      city + full state name
    "33.66,-95.55"      latitude, longitude
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import AmbiguousLocationError, LocationError, UnroutableLocationError
from .places import (
    NOT_ROAD_CONNECTED,
    STATE_NAMES,
    US_STATES,
    candidates,
    lookup,
    nearest_place,
)

# A point farther than this from every Census place is treated as outside the US
# (the Census places + county subdivisions cover the country densely).
MAX_MILES_FROM_US_PLACE = 25.0

LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# Contiguous US (lower 48 + DC), generous bounding box.
LOWER48_BBOX = (24.3, -125.0, 49.5, -66.8)  # south, west, north, east


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    lat: float
    lon: float


def resolve(query: str) -> Location:
    query = (query or "").strip()
    if not query:
        raise LocationError("Location is empty.")

    m = LATLON.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        s, w, n, e = LOWER48_BBOX
        if not (s <= lat <= n and w <= lon <= e):
            hint = " Did you swap them? Use 'latitude,longitude'." if (s <= lon <= n and w <= lat <= e) else ""
            raise LocationError(f"{query} is outside the contiguous United States.{hint}")
        # The box also covers parts of Canada and Mexico: require a US place nearby.
        place, miles = nearest_place(lat, lon)
        if miles > MAX_MILES_FROM_US_PLACE or place.state not in US_STATES or place.state in NOT_ROAD_CONNECTED:
            raise LocationError(f"{query} does not look like a location in the contiguous United States.")
        return Location(query, f"{lat:.4f}, {lon:.4f} (near {place.name}, {place.state})", lat, lon)

    parts = [p.strip() for p in query.split(",") if p.strip()]
    if len(parts) >= 3 and parts[-1].upper() in {"USA", "US", "UNITED STATES"}:
        parts = parts[:-1]
    if len(parts) < 2:
        options = [f"{p.name}, {p.state}" for p in candidates(parts[0])] if parts else []
        if options:
            raise AmbiguousLocationError(
                f"Please include the state: '{query}' matches places in "
                f"{len(options)} state(s).",
                candidates=options[:20],
            )
        raise LocationError(
            f"Could not understand '{query}'. Use 'City, ST' (e.g. 'Dallas, TX') or 'lat,lon'."
        )

    city, state_raw = ", ".join(parts[:-1]), parts[-1]
    state = state_raw.upper() if len(state_raw) == 2 else STATE_NAMES.get(state_raw.lower())
    if not state:
        raise LocationError(f"Unknown US state '{state_raw}'. Use a two-letter code like 'TX'.")
    if state in NOT_ROAD_CONNECTED:
        raise UnroutableLocationError(NOT_ROAD_CONNECTED[state])
    if state not in US_STATES:
        raise LocationError(f"Unknown US state '{state_raw}'. Use a two-letter code like 'TX'.")

    place = lookup(city, state)
    if place is None:
        others = [f"{p.name}, {p.state}" for p in candidates(city)]
        raise LocationError(
            f"'{city}, {state}' was not found in the US Census place list.",
            **({"did_you_mean": others[:20]} if others else {}),
        )
    lat, lon = place.route_point
    return Location(query, f"{place.name}, {place.state}", lat, lon)
