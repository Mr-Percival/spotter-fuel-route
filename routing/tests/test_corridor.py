import pytest

from routing.services.corridor import densify, haversine_miles, stations_along_route
from routing.services.stations import StationIndex


def _station(i, lat, lon, price=3.0):
    return {"opis_id": i, "name": f"S{i}", "address": "", "city": "", "state": "TX",
            "price": price, "lat": lat, "lon": lon, "geo_precision": "city"}


def test_densify_keeps_total_length_and_step():
    coords = [[-100.0, 35.0], [-95.0, 35.0]]
    lat, lon, cum = densify(coords, step_miles=1.0)
    assert cum[-1] == pytest.approx(haversine_miles(35.0, -100.0, 35.0, -95.0))
    assert max(cum[1:] - cum[:-1]) <= 1.0 + 1e-9


def test_station_mile_marker_and_corridor_filter():
    coords = [[-100.0, 35.0], [-95.0, 35.0]]  # ~283 mi due east
    idx = StationIndex.from_records([
        _station(1, 35.05, -99.0),   # ~3.5 mi north of the route, near mile 57
        _station(2, 35.0, -96.0),    # on the route near mile 226
        _station(3, 36.0, -97.0),    # ~69 mi away -> outside the corridor
    ])
    found = stations_along_route(coords, idx, corridor_miles=10)
    assert [s.station_id for s in found] == [1, 2]
    assert found[0].mile == pytest.approx(56.6, abs=1.5)
    assert found[0].off_route_miles == pytest.approx(3.5, abs=0.6)


def test_route_rescaled_to_reported_road_distance():
    coords = [[-100.0, 35.0], [-95.0, 35.0]]
    idx = StationIndex.from_records([_station(1, 35.0, -97.5)])  # halfway
    found = stations_along_route(coords, idx, corridor_miles=5, route_miles=300)
    assert found[0].mile == pytest.approx(150, abs=1.5)


def test_u_shaped_route_station_gets_a_single_mile_marker():
    # Out east along lat 35, back west along lat 35.1: the station sits between both legs.
    coords = [[-100.0, 35.0], [-96.0, 35.0], [-96.0, 35.1], [-100.0, 35.1]]
    idx = StationIndex.from_records([_station(1, 35.05, -98.0)])
    found = stations_along_route(coords, idx, corridor_miles=10)
    assert len(found) == 1
