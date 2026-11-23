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

from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Any, Sequence

EPS = 1e-9
INF = float("inf")


@dataclass(frozen=True)
class RouteStation:
    """A fuel station projected onto the route."""

    mile: float  # distance from the origin along the route
    price: float  # USD per gallon
    station_id: Any = None
    payload: dict = field(default_factory=dict, compare=False, hash=False)
    off_route_miles: float = 0.0  # one-way distance from the route to the station


@dataclass(frozen=True)
class FuelStop:
    station: RouteStation
    gallons: float
    cost: float  # gallons * price (fuel that goes into the trip)
    detour_cost: float = 0.0  # fuel burned leaving the route and coming back


@dataclass(frozen=True)
class FuelPlan:
    stops: list[FuelStop]
    total_cost: float  # money spent on fuel bought at the stops (incl. detour fuel)
    gallons_purchased: float
    initial_gallons: float
    total_miles: float
    stop_penalty: float = 0.0  # stop_cost * number of stops (not money spent on fuel)


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


def plan_fuel_stops_with_stop_cost(
    stations: Sequence[RouteStation],
    total_miles: float,
    *,
    tank_gallons: float = 50.0,
    mpg: float = 10.0,
    initial_gallons: float | None = None,
    stop_cost: float = 0.0,
) -> FuelPlan:
    """Exact optimum when every stop also has a fixed cost and a detour.

    Minimises  fuel bought + detour fuel + ``stop_cost`` x number of stops.
    The detour of a station ``off_route_miles`` away burns
    ``2 * off_route_miles / mpg`` gallons, paid at that station's price.

    Structure used (Khuller, Malekian & Mestre, "To fill or not to fill: the
    gas station problem", 2007): once the set of stops is fixed, the free-stop
    greedy above is optimal on it, and it only ever (a) fills the tank or
    (b) buys just enough to reach the next stop. So the fuel on arrival at a
    stop is either 0, ``tank - dist(j, i)/mpg`` for an earlier stop *j* that
    filled up, or what is left of the initial fuel. A DP over
    (stop, arrival state) is therefore exact with O(n * k^2) transitions,
    k = stations within one tank of range. With ``stop_cost = 0`` and no
    detours it returns the same cost as ``plan_fuel_stops`` (tested).
    """
    # Feasibility (and a precise error location) from the greedy, which reaches
    # the destination whenever any plan can.
    plan_fuel_stops(stations, total_miles, tank_gallons=tank_gallons, mpg=mpg,
                    initial_gallons=initial_gallons)

    tank = tank_gallons
    fuel0 = tank if initial_gallons is None else float(initial_gallons)
    if fuel0 * mpg + EPS >= total_miles:
        return FuelPlan([], 0.0, 0.0, fuel0, total_miles)

    max_range = tank * mpg
    nodes = sorted((s for s in stations if -EPS <= s.mile <= total_miles + EPS),
                   key=lambda s: (s.mile, s.price))
    miles = [s.mile for s in nodes]
    price = [s.price for s in nodes]
    fixed = [stop_cost + 2 * s.off_route_miles / mpg * s.price for s in nodes]
    n = len(nodes)

    # Arrival states at node i, keyed by how we got there:
    #   ("o",)   still running on the initial fuel        cost 0
    #   ("e",)   bought just enough at an earlier stop    arrival fuel 0
    #   ("f", j) filled up at earlier stop j              arrival tank - d(j, i)
    # Two facts make this fast (O(n k log k) instead of O(n k^2)):
    #   * "fill up at j" costs the same whatever the next stop is, so it is one
    #     number F[j] (+ back-pointer), shared by every later node in range;
    #   * "just enough from j to i" costs fixed[j] + need*price[j] +
    #     min over j's states with fuel a < need of (cost - a*price[j]),
    #     a prefix-minimum query over j's states sorted by fuel.
    F: list[float] = [INF] * n
    F_back: list = [None] * n
    E: list[float] = [INF] * n
    E_back: list = [None] * n
    # Per node: states sorted by arrival fuel, prefix minima of cost - a*price
    sorted_a: list[list[float]] = [[] for _ in range(n)]
    prefix_min: list[list[tuple[float, tuple, float]]] = [[] for _ in range(n)]

    finish = (INF, None)
    lo = 0  # first node within range behind i
    for i in range(n):
        while miles[i] - miles[lo] > max_range + EPS:
            lo += 1
        # 1) Best "arrived empty" cost: just enough from some earlier stop j.
        for j in range(lo, i):
            need = (miles[i] - miles[j]) / mpg
            k = bisect_left(sorted_a[j], need - EPS)  # states with a < need
            if k:
                val, key_j, a_j = prefix_min[j][k - 1]
                c = fixed[j] + need * price[j] + val
                if c < E[i] - EPS:
                    E[i], E_back[i] = c, (j, key_j, need - a_j)
        # 2) All arrival states at i: (fuel, cost, key).
        states = []
        if miles[i] <= fuel0 * mpg + EPS:
            states.append((fuel0 - miles[i] / mpg, 0.0, ("o",)))
        if E[i] < INF:
            states.append((0.0, E[i], ("e",)))
        for j in range(lo, i):
            if F[j] < INF:
                states.append((tank - (miles[i] - miles[j]) / mpg, F[j], ("f", j)))
        if not states:
            continue
        states.sort(key=lambda s: s[0])
        sorted_a[i] = [s[0] for s in states]
        best = (INF, None, 0.0)
        pm = []
        for a, c, key in states:
            v = c - a * price[i]
            if v < best[0]:
                best = (v, key, a)
            pm.append(best)
        prefix_min[i] = pm
        # 3) Fill up here (same cost for every next stop).
        for a, c, key in states:
            fill = tank - a
            if fill > EPS:
                cf = c + fixed[i] + fill * price[i]
                if cf < F[i] - EPS:
                    F[i], F_back[i] = cf, (key, fill)
        # 4) Finish from here.
        if total_miles - miles[i] <= max_range + EPS:
            need = (total_miles - miles[i]) / mpg
            for a, c, key in states:
                if a >= need - EPS and c < finish[0] - EPS:
                    finish = (c, (i, key, 0.0))  # enough fuel: drive past, no stop
            k = bisect_left(sorted_a[i], need - EPS)
            if k:
                val, key_i, a_i = pm[k - 1]
                c = fixed[i] + need * price[i] + val
                if c < finish[0] - EPS:
                    finish = (c, (i, key_i, need - a_i))

    if finish[1] is None:  # unreachable in practice: the greedy found a plan
        raise InfeasibleRouteError(0.0, total_miles, max_range)

    # Walk the back-pointers: (node, arrival key at node, gallons bought at node).
    stops: list[FuelStop] = []
    i, key, buy = finish[1]
    while True:
        if buy > EPS:
            st = nodes[i]
            stops.append(FuelStop(st, buy, buy * st.price,
                                  2 * st.off_route_miles / mpg * st.price))
        if key[0] == "o":
            break
        if key[0] == "e":
            i, key, buy = E_back[i]
        else:
            j = key[1]
            key, buy = F_back[j]
            i = j
    stops.reverse()

    fuel_cost = sum(s.cost + s.detour_cost for s in stops)
    return FuelPlan(stops, fuel_cost, sum(s.gallons for s in stops), fuel0, total_miles,
                    stop_penalty=stop_cost * len(stops))
