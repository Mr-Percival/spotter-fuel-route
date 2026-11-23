import pytest

from routing.services.errors import (
    AmbiguousLocationError,
    LocationError,
    UnroutableLocationError,
)
from routing.services.geocode import resolve


@pytest.mark.parametrize("query, label", [
    ("Paris, TX", "Paris, TX"),
    ("paris, tn", "Paris, TN"),
    ("Kansas City, KS", "Kansas City, KS"),
    ("Kansas City, Missouri", "Kansas City, MO"),
    ("Washington, DC", "Washington, DC"),
    ("Saint Louis, MO", "St. Louis, MO"),
    ("New York, NY, USA", "New York, NY"),
    ("Oklahoma City, OK", "Oklahoma City, OK"),
])
def test_city_state_resolves_to_the_right_state(query, label):
    assert resolve(query).label == label


def test_city_without_state_is_ambiguous_and_lists_candidates():
    with pytest.raises(AmbiguousLocationError) as exc:
        resolve("Springfield")
    states = {c.split(", ")[1] for c in exc.value.details["candidates"]}
    assert {"IL", "MO", "MA", "OH"} <= states


@pytest.mark.parametrize("query, fragment", [
    ("Anchorage, AK", "Canada"),
    ("Honolulu, HI", "no road connection"),
    ("San Juan, PR", "no road connection"),
    ("21.30,-157.85", "outside the contiguous"),
])
def test_us_places_without_a_road_link_are_unroutable_not_foreign(query, fragment):
    with pytest.raises(UnroutableLocationError) as exc:
        resolve(query)
    assert fragment in exc.value.message


def test_lat_lon_input():
    loc = resolve("40.71, -74.01")
    assert (loc.lat, loc.lon) == (40.71, -74.01)


@pytest.mark.parametrize("query", ["", "Dallas, XX", "Atlantis, TX"])
def test_invalid_locations(query):
    with pytest.raises(LocationError):
        resolve(query)
