"""Orchestrates one trip: geocode -> 1 routing call -> corridor -> optimise."""

from __future__ import annotations

import copy
import hashlib
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from . import ors_client
from .corridor import stations_along_route
from .errors import InfeasibleFuelPlanError
from .fuel_optimizer import InfeasibleRouteError, naive_plan_cost, plan_fuel_stops
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


def plan_trip(start: str, finish: str, initial_fuel_gallons: float | None = None) -> dict:
    timer = _Timer()
    tank, mpg = settings.VEHICLE_TANK_GALLONS, settings.VEHICLE_MPG
    initial = tank if initial_fuel_gallons is None else float(initial_fuel_gallons)

    origin, destination = resolve(start), resolve(finish)
    timer.lap("geocode")

    key = "trip:" + hashlib.sha256(
        f"{origin.lat:.5f},{origin.lon:.5f}|{destination.lat:.5f},{destination.lon:.5f}|{initial}|"
        f"{settings.ORS_PROFILE}|{settings.CORRIDOR_MILES}".encode()
    ).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        result = copy.deepcopy(cached)
        result["origin"]["query"], result["destination"]["query"] = start, finish
        result["meta"].update(cache="hit", routing_api_calls=0,
                              timings_ms={"total": timer.total()})
        return result

    route = ors_client.fetch_route(origin, destination)
    timer.lap("routing")

    index = get_station_index()
    candidates = stations_along_route(
        route.coordinates, index,
        corridor_miles=settings.CORRIDOR_MILES, route_miles=route.distance_miles,
    )
    timer.lap("corridor")

    try:
        plan = plan_fuel_stops(candidates, route.distance_miles,
                               tank_gallons=tank, mpg=mpg, initial_gallons=initial)
    except InfeasibleRouteError as exc:
        raise InfeasibleFuelPlanError(
            str(exc),
            gap_start_mile=round(exc.gap_start_mile, 1),
            gap_end_mile=round(exc.gap_end_mile, 1),
            stations_in_corridor=len(candidates),
        ) from exc
    naive = naive_plan_cost(candidates, route.distance_miles,
                            tank_gallons=tank, mpg=mpg, initial_gallons=initial)
    timer.lap("optimize")

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
            "off_route_miles": s.station.payload["off_route_miles"],
            "price_per_gallon": round(s.station.price, 3),
            "gallons": round(s.gallons, 2),
            "cost": round(s.cost, 2),
        }
        for n, s in enumerate(plan.stops, 1)
    ]

    warnings = []
    if any(s["geo_precision"] == "city" for s in stops):
        warnings.append("Some stops are located at their city's centroid because the "
                        "dataset lists highway exits rather than street addresses.")

    params = {"start": start, "finish": finish}
    if initial_fuel_gallons is not None:
        params["initial_fuel_gallons"] = initial
    result = {
        "origin": {"query": start, "resolved": origin.label, "lat": origin.lat, "lon": origin.lon},
        "destination": {"query": finish, "resolved": destination.label,
                        "lat": destination.lat, "lon": destination.lon},
        "distance_miles": round(route.distance_miles, 1),
        "duration_hours": round(route.duration_hours, 1),
        "assumptions": {
            "tank_gallons": tank,
            "mpg": mpg,
            "max_range_miles": tank * mpg,
            "initial_fuel_gallons": initial,
            "corridor_miles": settings.CORRIDOR_MILES,
            "price_rule": "lowest retail price per OPIS station id",
            "detour_cost": "ignored (stations within the corridor)",
            "total_cost_covers": "fuel purchased along the route",
        },
        "summary": {
            "total_cost": round(plan.total_cost, 2),
            "gallons_purchased": round(plan.gallons_purchased, 2),
            "stops": len(stops),
            "avg_price_paid": (round(plan.total_cost / plan.gallons_purchased, 3)
                               if plan.gallons_purchased else None),
            "savings_vs_naive": round(max(0.0, naive - plan.total_cost), 2),
        },
        "fuel_stops": stops,
        "route": {"type": "Feature", "properties": {},
                  "geometry": {"type": "LineString", "coordinates": route.coordinates}},
        "map_url": f"{reverse('route-map')}?{urlencode(params)}",
        "meta": {
            "routing_api_calls": 1,
            "routing_profile": settings.ORS_PROFILE,
            "stations_in_corridor": len(candidates),
            "cache": "miss",
            "timings_ms": {**timer.laps, "total": timer.total()},
        },
        "warnings": warnings,
    }
    cache.set(key, result)
    return result
