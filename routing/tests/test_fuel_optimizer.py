"""The greedy must match an exact dynamic programme and respect physical limits."""

import math
import random

import pytest

from routing.services.fuel_optimizer import (
    InfeasibleRouteError,
    RouteStation,
    plan_fuel_stops,
)


def exact_min_cost(stations, total, tank, initial):
    """Exact optimum by DP over integer fuel levels (mpg = 1, integer data).

    With integer distances and tank size, an optimal plan exists that buys
    whole gallons, so this enumerates every possible plan.
    """
    nodes = sorted(stations, key=lambda s: s.mile)
    # arrival[f] = cheapest cost to stand at the current node with f gallons
    first_leg = (nodes[0].mile if nodes else total)
    if initial < first_leg:
        return math.inf
    if not nodes:
        return 0.0
    arrival = {initial - first_leg: 0.0}
    for k, st in enumerate(nodes):
        nxt_mile = nodes[k + 1].mile if k + 1 < len(nodes) else total
        d = nxt_mile - st.mile
        nxt = {}
        for f, cost in arrival.items():
            for g in range(f, tank + 1):
                if g < d:
                    continue
                c = cost + (g - f) * st.price
                key = g - d
                if c < nxt.get(key, math.inf):
                    nxt[key] = c
        if not nxt:
            return math.inf
        arrival = nxt
    return min(arrival.values())


@pytest.mark.parametrize("seed", range(400))
def test_greedy_matches_exact_dp(seed):
    rng = random.Random(seed)
    tank = rng.randint(3, 12)
    total = rng.randint(tank + 1, tank * 6)
    miles = [rng.randint(0, total) for _ in range(rng.randint(0, 4))]
    if seed % 5:  # 80 % of cases: a feasible backbone of stations <= tank apart
        m = rng.randint(0, tank)
        while m < total:
            miles.append(m)
            m += rng.randint(1, tank)
    stations = [
        RouteStation(mile=m, price=round(rng.uniform(2.5, 5.0), 3), station_id=k)
        for k, m in enumerate(miles)
    ]
    initial = rng.randint(0, tank)
    expected = exact_min_cost(stations, total, tank, initial)
    if math.isinf(expected):
        with pytest.raises(InfeasibleRouteError):
            plan_fuel_stops(stations, total, tank_gallons=tank, mpg=1, initial_gallons=initial)
    else:
        plan = plan_fuel_stops(stations, total, tank_gallons=tank, mpg=1, initial_gallons=initial)
        assert plan.total_cost == pytest.approx(expected, abs=1e-6)


def _simulate(plan, total, tank, mpg):
    """Replay the plan and check the tank never runs dry or overflows."""
    fuel, pos = plan.initial_gallons, 0.0
    for stop in plan.stops:
        fuel -= (stop.station.mile - pos) / mpg
        assert fuel >= -1e-6, "ran out of fuel before a stop"
        fuel += stop.gallons
        assert fuel <= tank + 1e-6, "bought more than fits in the tank"
        pos = stop.station.mile
    fuel -= (total - pos) / mpg
    assert fuel >= -1e-6, "ran out of fuel before the destination"
    return fuel


@pytest.mark.parametrize("seed", range(100))
def test_plan_is_physically_valid_at_real_scale(seed):
    rng = random.Random(1000 + seed)
    total = rng.uniform(50, 3000)
    stations = [
        RouteStation(mile=rng.uniform(0, total), price=rng.uniform(2.7, 6.4), station_id=k)
        for k in range(rng.randint(5, 80))
    ]
    initial = rng.choice([0.0, 25.0, 50.0])
    try:
        plan = plan_fuel_stops(stations, total, initial_gallons=initial)
    except InfeasibleRouteError:
        return
    leftover = _simulate(plan, total, 50, 10)
    # Nothing is wasted: we arrive (almost) empty unless the initial fuel was enough.
    assert leftover <= max(0.0, initial - total / 10) + 1e-6
    assert plan.gallons_purchased + plan.initial_gallons - leftover == pytest.approx(total / 10)


def test_short_trip_on_initial_tank_needs_no_stops():
    plan = plan_fuel_stops([RouteStation(100, 3.0)], 400)
    assert plan.stops == [] and plan.total_cost == 0


def test_buys_only_enough_to_reach_cheaper_station():
    s1, s2 = RouteStation(0, 4.0, "a"), RouteStation(200, 3.0, "b")
    plan = plan_fuel_stops([s1, s2], 600, initial_gallons=0)
    assert [(s.station.station_id, round(s.gallons, 6)) for s in plan.stops] == [("a", 20), ("b", 40)]
    assert plan.total_cost == pytest.approx(20 * 4 + 40 * 3)


def test_fills_up_when_current_station_is_cheapest():
    s1, s2 = RouteStation(0, 3.0, "a"), RouteStation(450, 4.0, "b")
    plan = plan_fuel_stops([s1, s2], 700, initial_gallons=0)
    assert [(s.station.station_id, round(s.gallons, 6)) for s in plan.stops] == [("a", 50), ("b", 20)]


def test_gap_longer_than_range_is_reported_with_its_location():
    stations = [RouteStation(0, 3.0), RouteStation(300, 3.0), RouteStation(900, 3.0)]
    with pytest.raises(InfeasibleRouteError) as exc:
        plan_fuel_stops(stations, 1000, initial_gallons=0)
    assert (exc.value.gap_start_mile, exc.value.gap_end_mile) == (300, 900)


def test_empty_tank_without_station_at_origin_is_infeasible():
    with pytest.raises(InfeasibleRouteError) as exc:
        plan_fuel_stops([RouteStation(30, 3.0)], 800, initial_gallons=0)
    assert exc.value.gap_start_mile == 0


# --- Stop cost + detour: exact DP over (stop, arrival state) -----------------

from routing.services.fuel_optimizer import plan_fuel_stops_with_stop_cost  # noqa: E402


def exact_min_cost_with_stops(stations, total, tank, initial, stop_cost):
    """Brute force over integer fuel levels (mpg = 1), charging a fixed cost and
    the round-trip detour fuel whenever a station sells anything."""
    nodes = sorted(stations, key=lambda s: s.mile)
    first_leg = nodes[0].mile if nodes else total
    if initial < first_leg:
        return math.inf
    if initial >= total:
        return 0.0
    arrival = {initial - first_leg: 0.0}
    for k, st in enumerate(nodes):
        d = (nodes[k + 1].mile if k + 1 < len(nodes) else total) - st.mile
        fixed = stop_cost + 2 * st.off_route_miles * st.price
        nxt = {}
        for f, cost in arrival.items():
            for g in range(f, tank + 1):
                if g < d:
                    continue
                c = cost + (g - f) * st.price + (fixed if g > f else 0.0)
                if c < nxt.get(g - d, math.inf):
                    nxt[g - d] = c
        if not nxt:
            return math.inf
        arrival = nxt
    return min(arrival.values())


def _random_instance(seed):
    rng = random.Random(seed)
    tank = rng.randint(3, 10)
    total = rng.randint(tank + 1, tank * 5)
    miles = [rng.randint(0, total) for _ in range(rng.randint(0, 3))]
    m = rng.randint(0, tank)
    while m < total:
        miles.append(m)
        m += rng.randint(1, tank)
    stations = [
        RouteStation(mile=m, price=round(rng.uniform(2.5, 5.0), 3), station_id=k,
                     off_route_miles=rng.choice([0, 0, 0.5, 1.5]))
        for k, m in enumerate(miles)
    ]
    return rng, tank, total, stations, rng.randint(0, tank)


@pytest.mark.parametrize("seed", range(300))
def test_stop_cost_dp_matches_brute_force(seed):
    rng, tank, total, stations, initial = _random_instance(seed)
    stop_cost = rng.choice([0.0, 1.0, 5.0, 25.0])
    expected = exact_min_cost_with_stops(stations, total, tank, initial, stop_cost)
    if math.isinf(expected):
        with pytest.raises(InfeasibleRouteError):
            plan_fuel_stops_with_stop_cost(stations, total, tank_gallons=tank, mpg=1,
                                           initial_gallons=initial, stop_cost=stop_cost)
        return
    plan = plan_fuel_stops_with_stop_cost(stations, total, tank_gallons=tank, mpg=1,
                                          initial_gallons=initial, stop_cost=stop_cost)
    assert plan.total_cost + plan.stop_penalty == pytest.approx(expected, abs=1e-6)
    _simulate(plan, total, tank, 1)


@pytest.mark.parametrize("seed", range(100))
def test_zero_stop_cost_reduces_to_the_greedy(seed):
    rng = random.Random(5000 + seed)
    total = rng.uniform(100, 2500)
    stations = [RouteStation(mile=rng.uniform(0, total), price=rng.uniform(2.7, 6.4), station_id=k)
                for k in range(rng.randint(10, 80))]
    try:
        greedy = plan_fuel_stops(stations, total, initial_gallons=0 if seed % 2 else 50)
    except InfeasibleRouteError:
        return
    dp = plan_fuel_stops_with_stop_cost(stations, total, initial_gallons=0 if seed % 2 else 50)
    assert dp.total_cost == pytest.approx(greedy.total_cost, abs=1e-6)


def test_stop_cost_avoids_tiny_top_ups():
    # Free stops: buy 1 gallon at the cheap-ish station, then the rest further on.
    stations = [RouteStation(0, 3.00, "a"), RouteStation(10, 3.01, "b"), RouteStation(400, 2.90, "c")]
    free = plan_fuel_stops_with_stop_cost(stations, 800, initial_gallons=0)
    costly = plan_fuel_stops_with_stop_cost(stations, 800, initial_gallons=0, stop_cost=25)
    assert len(costly.stops) < len(free.stops) or len(free.stops) == 2
    assert all(s.station.station_id != "b" for s in costly.stops)
