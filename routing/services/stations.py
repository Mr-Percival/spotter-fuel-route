"""In-memory spatial index of all stations, built lazily once per process.

Stations are stored as 3-D unit vectors so that a plain KD-tree (scipy's
cKDTree) answers great-circle radius queries: two points on the sphere are
within angle theta iff their chord distance is <= 2*sin(theta/2).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_MILES = 3958.8


def to_unit_vectors(lat_deg, lon_deg) -> np.ndarray:
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))


def miles_to_chord(miles: float) -> float:
    return 2 * np.sin(miles / EARTH_RADIUS_MILES / 2)


def chord_to_miles(chord):
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.clip(np.asarray(chord) / 2, 0, 1))


@dataclass
class StationIndex:
    records: list[dict]  # one dict per station, same order as the tree
    prices: np.ndarray
    tree: cKDTree

    @classmethod
    def from_records(cls, records: list[dict]) -> "StationIndex":
        if not records:
            raise RuntimeError("No stations loaded. Run `python manage.py load_stations`.")
        xyz = to_unit_vectors([r["lat"] for r in records], [r["lon"] for r in records])
        return cls(records, np.array([r["price"] for r in records]), cKDTree(xyz))


_index: StationIndex | None = None
_lock = threading.Lock()


def get_station_index() -> StationIndex:
    """Build the index from the database on first use (not at import/app-ready time,
    so management commands and migrations don't pay for it)."""
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                from routing.models import Station

                rows = Station.objects.values(
                    "opis_id", "name", "address", "city", "state",
                    "price", "latitude", "longitude", "geo_precision",
                )
                records = [
                    {
                        "opis_id": r["opis_id"], "name": r["name"], "address": r["address"],
                        "city": r["city"], "state": r["state"], "price": float(r["price"]),
                        "lat": r["latitude"], "lon": r["longitude"],
                        "geo_precision": r["geo_precision"],
                    }
                    for r in rows
                ]
                _index = StationIndex.from_records(records)
    return _index


def reset_station_index() -> None:
    global _index
    _index = None
