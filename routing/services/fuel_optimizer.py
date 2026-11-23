"""Cost-optimal refuelling plan along a fixed route (pure Python, no Django).

Problem
-------
A vehicle drives a route of ``total_miles``. Fuel stations sit at known
mile markers along that route, each with a price per gallon. The tank holds
``tank_gallons`` and the vehicle does ``mpg`` miles per gallon, so its range on
a full tank is ``tank_gallons * mpg`` miles. Stopping is free and any amount
of fuel can be bought. Minimise the money spent on fuel.

Algorithm (classic "gas station" greedy, O(n log n) with the sort)
------------------------------------------------------------------
Standing at station *i* with ``fuel`` gallons in the tank:

1. If some station *j* within one full tank of range is **cheaper** than *i*,
   buy only enough to reach the *first* such *j*, and drive there.
2. Otherwise, if the destination is within range, buy only enough to reach it.
3. Otherwise fill the tank and drive to the **cheapest** station within range.

Why it is optimal (exchange argument): fuel bought at *i* is only ever used
before reaching a cheaper station, because once we arrive at a cheaper station
any gallon we still need could have been bought there for less. When no
cheaper station is reachable, every gallon needed in the next window is at
least as expensive anywhere else, so buying it at *i* (up to a full tank) can
never hurt. ``tests/test_fuel_optimizer.py`` checks this against an exact
dynamic programme on random instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

EPS = 1e-9


@dataclass(frozen=True)
class RouteStation:
    """A fuel station projected onto the route."""

    mile: float  # distance from the origin along the route
    price: float  # USD per gallon
    station_id: Any = None
    payload: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class FuelStop:
    station: RouteStation
    gallons: float
    cost: float


@dataclass(frozen=True)
class FuelPlan:
    stops: list[FuelStop]
    total_cost: float
    gallons_purchased: float
    initial_gallons: float
    total_miles: float


class InfeasibleRouteError(Exception):
    """No sequence of stops can bridge a stretch of the route."""

    def __init__(self, gap_start_mile: float, gap_end_mile: float, max_range: float):
        self.gap_start_mile = gap_start_mile
        self.gap_end_mile = gap_end_mile
        self.max_range = max_range
        super().__init__(
            f"No fuel station between mile {gap_start_mile:.1f} and mile "
            f"{gap_end_mile:.1f} ({gap_end_mile - gap_start_mile:.1f} mi); "
            f"the vehicle can only cover {max_range:.0f} mi without refuelling."
        )


def plan_fuel_stops(
    stations: Sequence[RouteStation],
    total_miles: float,
    *,
    tank_gallons: float = 50.0,
    mpg: float = 10.0,
    initial_gallons: float | None = None,
) -> FuelPlan:
    """Return the cheapest refuelling plan, or raise ``InfeasibleRouteError``.

    ``initial_gallons`` defaults to a full tank. Stations outside
    ``[0, total_miles]`` are ignored.
    """
    if tank_gallons <= 0 or mpg <= 0:
        raise ValueError("tank_gallons and mpg must be positive")
    if total_miles < 0:
        raise ValueError("total_miles must be non-negative")
    fuel = tank_gallons if initial_gallons is None else float(initial_gallons)
    if not 0 <= fuel <= tank_gallons + EPS:
        raise ValueError("initial_gallons must be between 0 and tank_gallons")

    max_range = tank_gallons * mpg
    route = sorted(
        (s for s in stations if -EPS <= s.mile <= total_miles + EPS),
        key=lambda s: (s.mile, s.price),
    )

    stops: list[FuelStop] = []
    pos = 0.0
    i = -1  # index of the station we stand at; -1 = origin (nothing to buy)

    # Leaving the origin: only the initial fuel is available.
    if fuel * mpg + EPS < total_miles:
        if not route or route[0].mile - pos > fuel * mpg + EPS:
            gap_end = route[0].mile if route else total_miles
            raise InfeasibleRouteError(pos, gap_end, fuel * mpg)
        fuel -= (route[0].mile - pos) / mpg
        pos, i = route[0].mile, 0
    else:
        return FuelPlan([], 0.0, 0.0, _initial(tank_gallons, initial_gallons), total_miles)

    while True:
        here = route[i]
        reach = pos + max_range

        # Stations reachable on a full tank from here.
        j = i + 1
        cheaper = None
        cheapest = None
        while j < len(route) and route[j].mile <= reach + EPS:
            if cheaper is None and route[j].price < here.price - EPS:
                cheaper = j
            if cheapest is None or route[j].price < route[cheapest].price - EPS or (
                abs(route[j].price - route[cheapest].price) <= EPS
            ):
                # tie -> prefer the farther one (fewer stops, same cost)
                cheapest = j
            j += 1

        if cheaper is not None:
            target_mile, nxt = route[cheaper].mile, cheaper
        elif total_miles <= reach + EPS:
            target_mile, nxt = total_miles, None
        elif cheapest is not None:
            target_mile, nxt = route[cheapest].mile, cheapest
        else:
            gap_end = route[i + 1].mile if i + 1 < len(route) else total_miles
            raise InfeasibleRouteError(pos, gap_end, max_range)

        needed = (target_mile - pos) / mpg
        if cheaper is None and nxt is not None:
            buy = tank_gallons - fuel  # no cheaper fuel ahead: fill up
        else:
            buy = max(0.0, needed - fuel)
        if buy > EPS:
            stops.append(FuelStop(here, buy, buy * here.price))
            fuel += buy
        fuel -= needed
        pos = target_mile
        if nxt is None:
            break
        i = nxt

    total = sum(s.cost for s in stops)
    bought = sum(s.gallons for s in stops)
    return FuelPlan(stops, total, bought, _initial(tank_gallons, initial_gallons), total_miles)


def naive_plan_cost(
    stations: Sequence[RouteStation],
    total_miles: float,
    *,
    tank_gallons: float = 50.0,
    mpg: float = 10.0,
    initial_gallons: float | None = None,
) -> float:
    """Baseline a driver without price data would follow: keep driving and only
    stop when the next station would be out of reach, then fill up (never buying
    more than needed to finish). Used to report the optimiser's savings.

    Assumes the trip is feasible (call it after ``plan_fuel_stops`` succeeded).
    """
    fuel = _initial(tank_gallons, initial_gallons)
    pos, cost = 0.0, 0.0
    route = sorted((s for s in stations if 0 <= s.mile <= total_miles), key=lambda s: s.mile)
    for k, s in enumerate(route):
        fuel -= (s.mile - pos) / mpg
        pos = s.mile
        nxt = route[k + 1].mile if k + 1 < len(route) else total_miles
        if (nxt - pos) / mpg > fuel + EPS:
            buy = min(tank_gallons - fuel, (total_miles - pos) / mpg - fuel)
            cost += buy * s.price
            fuel += buy
    return cost


def _initial(tank_gallons: float, initial_gallons: float | None) -> float:
    return tank_gallons if initial_gallons is None else float(initial_gallons)
