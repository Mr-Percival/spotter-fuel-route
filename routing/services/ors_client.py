"""OpenRouteService directions: exactly one HTTP call per route."""

from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings

from .errors import (
    NoRouteError,
    RoutingRateLimitError,
    RoutingServiceError,
    RoutingTimeoutError,
)
from .geocode import Location

METERS_PER_MILE = 1609.344

# ORS error codes that mean "this trip cannot be routed", not "ORS is down".
# https://giscience.github.io/openrouteservice/api-reference/error-codes
_NO_ROUTE_CODES = {2004, 2009, 2010, 2099}


@dataclass(frozen=True)
class Route:
    coordinates: list[list[float]]  # [[lon, lat], ...] as returned by ORS (GeoJSON order)
    distance_miles: float
    duration_hours: float


def fetch_route(origin: Location, destination: Location) -> Route:
    if not settings.ORS_API_KEY:
        raise RoutingServiceError("ORS_API_KEY is not configured on the server.")

    url = f"{settings.ORS_BASE_URL}/v2/directions/{settings.ORS_PROFILE}/geojson"
    body = {
        "coordinates": [[origin.lon, origin.lat], [destination.lon, destination.lat]],
        "instructions": False,
        "elevation": False,
        # City centroids are not always on a road: let ORS snap to the nearest one.
        "radiuses": [-1, -1],
        # Keep US -> US trips inside the US (e.g. Detroit -> Buffalo via Ontario).
        "options": {"avoid_borders": "all"},
    }
    try:
        resp = requests.post(
            url,
            json=body,
            headers={"Authorization": settings.ORS_API_KEY,
                     "Accept": "application/geo+json, application/json"},
            timeout=settings.ORS_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise RoutingTimeoutError("The routing service did not answer in time.") from exc
    except requests.RequestException as exc:
        raise RoutingServiceError("Could not reach the routing service.") from exc

    if resp.status_code == 429:
        raise RoutingRateLimitError("Routing service rate limit reached; try again in a minute.")
    payload = _json(resp)
    if resp.status_code != 200:
        err = (payload or {}).get("error", {})
        code = err.get("code") if isinstance(err, dict) else None
        message = err.get("message") if isinstance(err, dict) else str(err)
        if code in _NO_ROUTE_CODES:
            raise NoRouteError(f"No drivable route between these points: {message}", ors_code=code)
        raise RoutingServiceError(
            f"Routing service error (HTTP {resp.status_code}).", ors_code=code
        )

    try:
        feature = payload["features"][0]
        summary = feature["properties"]["summary"]
        return Route(
            coordinates=feature["geometry"]["coordinates"],
            distance_miles=summary.get("distance", 0.0) / METERS_PER_MILE,
            duration_hours=summary.get("duration", 0.0) / 3600,
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise RoutingServiceError("Unexpected response from the routing service.") from exc


def _json(resp):
    try:
        return resp.json()
    except ValueError:
        return None
