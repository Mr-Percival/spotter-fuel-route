"""Offline lookup of US place names -> coordinates (Census Gazetteer).

Used both to geocode the request's start/finish ("City, ST") without any
external call and as the fallback coordinate for fuel stations whose street
address cannot be geocoded.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PLACES_CSV = DATA_DIR / "us_places.csv"

# 50 states + DC. Territories and Canadian provinces are handled separately.
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
    "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
# Reachable by road from the lower 48 only through Canada, or not at all.
NOT_ROAD_CONNECTED = {
    "AK": "Alaska is only reachable by road through Canada; this planner keeps routes inside the contiguous US.",
    "HI": "Hawaii has no road connection to the mainland.",
    "PR": "Puerto Rico has no road connection to the mainland.",
    "VI": "The US Virgin Islands have no road connection to the mainland.",
    "GU": "Guam has no road connection to the mainland.",
    "AS": "American Samoa has no road connection to the mainland.",
    "MP": "The Northern Mariana Islands have no road connection to the mainland.",
}
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR",
}

_ABBREV = [
    (re.compile(r"\bsaint\b"), "st"), (re.compile(r"\bsainte\b"), "st"),
    (re.compile(r"\bste\b"), "st"),
    (re.compile(r"\bfort\b"), "ft"), (re.compile(r"\bmount\b"), "mt"),
    (re.compile(r"\bpoint\b"), "pt"),
]


def normalize(name: str) -> str:
    """'St. Louis' / 'Saint Louis' / 'ST LOUIS' -> 'stlouis'."""
    s = name.lower().replace(".", " ").replace("'", "").replace("-", " ")
    for rx, rep in _ABBREV:
        s = rx.sub(rep, s)
    return re.sub(r"[^a-z0-9]", "", s)


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lon: float


@lru_cache(maxsize=1)
def _index() -> tuple[dict[tuple[str, str], Place], dict[str, list[Place]]]:
    by_key: dict[tuple[str, str], tuple[tuple[int, float], Place]] = {}
    by_name: dict[str, list[Place]] = {}
    with open(PLACES_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = Place(r["name"], r["state"], float(r["lat"]), float(r["lon"]))
            key = (normalize(p.name), p.state)
            # Prefer a Census "place" over a county subdivision; among equals,
            # keep the larger one (a city over a same-named CDP).
            rank = (1 if r["source"] == "place" else 0, float(r["land_sqmi"] or 0))
            if key not in by_key or rank > by_key[key][0]:
                by_key[key] = (rank, p)
    for (n, _), (_, p) in by_key.items():
        by_name.setdefault(n, []).append(p)
    return {k: v[1] for k, v in by_key.items()}, by_name


def lookup(city: str, state: str) -> Place | None:
    return _index()[0].get((normalize(city), state.upper()))


def candidates(city: str) -> list[Place]:
    """All states that have a place with this name (for ambiguity messages)."""
    return sorted(_index()[1].get(normalize(city), []), key=lambda p: p.state)
