"""Orchestrates one trip: geocode -> 1 routing call -> corridor -> optimise.

Pure service layer: knows nothing about HTTP (URLs and status codes live in
the views / error classes).
"""

from __future__ import annotations

import hashlib
import time

from django.conf import settings
from django.core.cache import cache

from . import ors_client
from .corridor import stations_along_route
from .errors import InfeasibleFuelPlanError, LocationError, StationsNotLoadedError
from .fuel_optimizer import (
    InfeasibleRouteError,
    RouteStation,
    naive_plan_cost,
    plan_fuel_stops_with_stop_cost,
)
from .geocode import resolve
from .stations import get_station_index


class _Timer:
    def __init__(self):
        self.t0 = self.last = time.perf_counter()
        self.laps: dict[str, float] = {}

    def lap(self, name: str) -> None:
        now = time.perf_counter()
        self.laps[name] = round((now - self.last) * 1000, 1)
        self.last = now

    def total(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 1)


def plan_trip(
    start: str,
    finish: str,
    initial_fuel_gallons: float | None = None,
    stop_cost_usd: float | None = None,
) -> dict:
    """Plan the cheapest refuelling for a trip.

    ``initial_fuel_gallons=None`` (default) means the trip pays for all of its
    fuel: the truck leaves with just enough to reach the first station on the
    route, and that fuel is charged at that station's price. Passing a number
    means that fuel is already in the tank (and already paid for).
    """
    timer = _Timer()
    tank, mpg = settings.VEHICLE_TANK_GALLONS, settings.VEHICLE_MPG
    stop_cost = settings.STOP_COST_USD if stop_cost_usd is None else float(stop_cost_usd)

    origin, destination = resolve(start), resolve(finish)
    if (round(origin.lat, 4), round(origin.lon, 4)) == (round(destination.lat, 4),
                                                        round(destination.lon, 4)):
        raise LocationError("Start and finish are the same place.")
    timer.lap("geocode")

    key = "trip:" + hashlib.sha256(
        f"{origin.lat:.5f},{origin.lon:.5f}|{destination.lat:.5f},{destination.lon:.5f}|"
        f"{initial_fuel_gallons}|{stop_cost}|{settings.ORS_PROFILE}|{settings.CORRIDOR_MILES}"
        .encode()
    ).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return {
            **cached,
            "origin": {**cached["origin"], "query": start},
            "destination": {**cached["destination"], "query": finish},
            "meta": {**cached["meta"], "cache": "hit", "routing_api_calls": 0,
                     "timings_ms": {"total": timer.total()}},
        }

    # Fail fast (and without spending routing quota) if the data isn't loaded.
    try:
        index = get_station_index()
    except RuntimeError as exc:
        raise StationsNotLoadedError(str(exc)) from exc

    route = ors_client.fetch_route(origin, destination)
    timer.lap("routing")

    candidates = stations_along_route(
        route.coordinates, index,
        corridor_miles=settings.CORRIDOR_MILES, route_miles=route.distance_miles,
    )
    timer.lap("corridor")

    total = route.distance_miles
    origin_fuel = _origin_fuel(candidates, total, tank, mpg, initial_fuel_gallons, index, origin)
    initial = origin_fuel["gallons"] if initial_fuel_gallons is None else float(initial_fuel_gallons)

    try:
        plan = plan_fuel_stops_with_stop_cost(
            candidates, total, tank_gallons=tank, mpg=mpg,
            initial_gallons=initial, stop_cost=stop_cost,
        )
    except InfeasibleRouteError as exc:
        raise InfeasibleFuelPlanError(
            str(exc),
            gap_start_mile=round(exc.gap_start_mile, 1),
            gap_end_mile=round(exc.gap_end_mile, 1),
            stations_in_corridor=len(candidates),
        ) from exc
    naive = naive_plan_cost(candidates, total, tank_gallons=tank, mpg=mpg, initial_gallons=initial)
    timer.lap("optimize")

    origin_cost = origin_fuel["cost"] if initial_fuel_gallons is None else 0.0
    total_cost = origin_cost + plan.total_cost
    result = _response(origin, destination, route, candidates, plan, origin_fuel,
                       initial_fuel_gallons, stop_cost, total_cost, naive + origin_cost)
    result["meta"]["timings_ms"] = {**timer.laps, "total": timer.total()}
    cache.set(key, result)
    return result


def _origin_fuel(candidates, total, tank, mpg, initial_fuel_gallons, index, origin) -> dict:
    """Fuel the truck needs to reach its first station, priced at that station."""
    if initial_fuel_gallons is not None:
        return {"gallons": float(initial_fuel_gallons), "price_per_gallon": None, "cost": 0.0,
                "priced_at": "already in the tank (not charged)"}
    if candidates:
        # Cheapest of the station(s) at the first mile marker (several can share it).
        first: RouteStation = min((c for c in candidates if c.mile <= candidates[0].mile + 0.5),
                                  key=lambda c: c.price)
        gallons = min(first.mile, total) / mpg
        price, where = first.price, f"first station on the route ({first.payload['name']}, " \
                                    f"{first.payload['city']}, {first.payload['state']}, mile {first.mile:.1f})"
    else:  # short trip with no station on the route: nearest station to the origin
        from .stations import to_unit_vectors
        _, i = index.tree.query(to_unit_vectors([origin.lat], [origin.lon])[0])
        rec = index.records[int(i)]
        gallons = total / mpg
        price, where = rec["price"], f"nearest station to the origin ({rec['name']}, {rec['city']}, {rec['state']})"
    gallons = min(gallons, tank)  # if the first station is beyond range, the optimiser reports the gap
    # Unrounded: the optimiser needs the exact amount (rounding down would strand the truck).
    return {"gallons": gallons, "price_per_gallon": price, "cost": gallons * price,
            "priced_at": where}


def _response(origin, destination, route, candidates, plan, origin_fuel, initial_fuel_gallons,
              stop_cost, total_cost, naive_cost) -> dict:
    stops = [
        {
            "sequence": n,
            "opis_id": s.station.payload["opis_id"],
            "name": s.station.payload["name"],
            "address": s.station.payload["address"],
            "city": s.station.payload["city"],
            "state": s.station.payload["state"],
            "lat": s.station.payload["lat"],
            "lon": s.station.payload["lon"],
            "geo_precision": s.station.payload["geo_precision"],
            "mile": round(s.station.mile, 1),
            "off_route_miles": s.station.off_route_miles,
            "price_per_gallon": round(s.station.price, 3),
            "gallons": round(s.gallons, 2),
            "cost": round(s.cost + s.detour_cost, 2),
        }
        for n, s in enumerate(plan.stops, 1)
    ]
    trip_gallons = route.distance_miles / settings.VEHICLE_MPG
    warnings = []
    if any(s["geo_precision"] == "city" for s in stops):
        warnings.append("Some stops are located at their city's centroid because the "
                        "dataset lists highway exits rather than street addresses.")
    if initial_fuel_gallons is None and candidates and candidates[0].mile > 50:
        warnings.append(f"The first station on the route is at mile {candidates[0].mile:.0f}; "
                        "fuel for that stretch is priced at that station.")

    return {
        "origin": {"query": origin.query, "resolved": origin.label,
                   "lat": origin.lat, "lon": origin.lon},
        "destination": {"query": destination.query, "resolved": destination.label,
                        "lat": destination.lat, "lon": destination.lon},
        "distance_miles": round(route.distance_miles, 1),
        "duration_hours": round(route.duration_hours, 1),
        "assumptions": {
            "tank_gallons": settings.VEHICLE_TANK_GALLONS,
            "mpg": settings.VEHICLE_MPG,
            "max_range_miles": settings.VEHICLE_TANK_GALLONS * settings.VEHICLE_MPG,
            "initial_fuel": ("enough to reach the first station on the route, charged at its price"
                             if initial_fuel_gallons is None
                             else f"{initial_fuel_gallons} gallons already in the tank"),
            "stop_cost_usd": stop_cost,
            "corridor_miles": settings.CORRIDOR_MILES,
            "detour": "round trip to an off-route station is paid in fuel at its price",
            "price_rule": "lowest retail price per OPIS station id",
        },
        "summary": {
            "total_cost": round(total_cost, 2),
            "trip_fuel_gallons": round(trip_gallons, 2),
            "stops": len(stops),
            "gallons_bought_at_stops": round(plan.gallons_purchased, 2),
            "origin_fuel": {
                **origin_fuel,
                "gallons": round(origin_fuel["gallons"], 2),
                "cost": round(origin_fuel["cost"], 2),
                "price_per_gallon": (round(origin_fuel["price_per_gallon"], 3)
                                     if origin_fuel["price_per_gallon"] is not None else None),
            },
            "effective_price_per_gallon": (round(total_cost / trip_gallons, 3) if trip_gallons else None),
            "savings_vs_naive": round(naive_cost - total_cost, 2),
        },
        "fuel_stops": stops,
        "route": {"type": "Feature", "properties": {},
                  "geometry": {"type": "LineString", "coordinates": route.coordinates}},
        "meta": {
            "routing_api_calls": 1,
            "routing_profile": settings.ORS_PROFILE,
            "stations_in_corridor": len(candidates),
            "cache": "miss",
        },
        "warnings": warnings,
    }
