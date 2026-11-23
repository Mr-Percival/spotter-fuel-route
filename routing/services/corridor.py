"""Find the stations along a route and their mile marker on it.

1. Densify the route polyline so consecutive points are <= ``step_miles`` apart
   and compute the cumulative distance of each point.
2. Build a KD-tree over those route points (3-D unit vectors) and, in a single
   vectorised query, find for every station its nearest route point within the
   corridor. That point's cumulative distance is the station's mile marker.

Cost: O(S log P) for S stations and P route points (~5k stations x ~3k points
for a coast-to-coast trip takes a few milliseconds).
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from .fuel_optimizer import RouteStation
from .stations import (
    EARTH_RADIUS_MILES,
    StationIndex,
    chord_to_miles,
    miles_to_chord,
    to_unit_vectors,
)


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    h = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(h))


def densify(coords_lonlat, step_miles: float = 1.0):
    """Return (lat, lon, cumulative_miles) arrays with points <= step apart."""
    pts = np.asarray(coords_lonlat, dtype=float)
    lon, lat = pts[:, 0], pts[:, 1]
    if len(pts) == 1:
        return lat, lon, np.zeros(1)
    seg = haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:])
    n = np.maximum(1, np.ceil(seg / step_miles).astype(int))  # pieces per segment
    seg_idx = np.repeat(np.arange(len(seg)), n)
    offsets = np.concatenate([np.arange(k) for k in n]) / np.repeat(n, n)
    out_lat = np.append(lat[seg_idx] + (lat[seg_idx + 1] - lat[seg_idx]) * offsets, lat[-1])
    out_lon = np.append(lon[seg_idx] + (lon[seg_idx + 1] - lon[seg_idx]) * offsets, lon[-1])
    cum_start = np.concatenate(([0.0], np.cumsum(seg)))
    cum = np.append(cum_start[seg_idx] + seg[seg_idx] * offsets, cum_start[-1])
    return out_lat, out_lon, cum


def stations_along_route(
    coords_lonlat,
    index: StationIndex,
    *,
    corridor_miles: float,
    route_miles: float | None = None,
    step_miles: float = 1.0,
) -> list[RouteStation]:
    lat, lon, cum = densify(coords_lonlat, step_miles)
    # The polyline length is a hair shorter than the road distance ORS reports;
    # rescale so mile markers are consistent with the reported total.
    scale = (route_miles / cum[-1]) if route_miles and cum[-1] > 0 else 1.0

    route_tree = cKDTree(to_unit_vectors(lat, lon))
    station_xyz = index.tree.data
    dist, idx = route_tree.query(station_xyz, distance_upper_bound=miles_to_chord(corridor_miles))
    hits = np.flatnonzero(np.isfinite(dist))

    out = []
    for s in hits:
        rec = index.records[s]
        out.append(RouteStation(
            mile=float(cum[idx[s]] * scale),
            price=rec["price"],
            station_id=rec["opis_id"],
            payload={**rec, "off_route_miles": round(float(chord_to_miles(dist[s])), 1)},
        ))
    out.sort(key=lambda r: r.mile)
    return out
