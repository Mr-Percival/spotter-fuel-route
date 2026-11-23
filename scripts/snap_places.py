"""Offline, one-time: snap every place in data/us_places.csv to the nearest truck road.

Why: a Census "internal point" is only guaranteed to be inside the place's
boundary, which may include water or open land (San Francisco's falls in the
Pacific because the city includes the Farallon Islands). OpenRouteService only
snaps a route endpoint within 350 m of a road, so ~17 % of large places failed.
The ORS snap endpoint accepts up to ~5 km and 2,000 points per call, so the
whole table is fixed with ~35 calls, once, and the API keeps using exactly one
routing call per request.

Usage:  python scripts/snap_places.py      (needs ORS_API_KEY in .env)
Adds columns road_lat, road_lon (empty when no road within 5 km).
"""

import csv
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402

PLACES = ROOT / "data" / "us_places.csv"
BATCH = 2000
RADIUS_M = 5000


def main() -> None:
    with open(PLACES, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    url = f"{settings.ORS_BASE_URL}/v2/snap/{settings.ORS_PROFILE}"
    headers = {"Authorization": settings.ORS_API_KEY}
    snapped = 0
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        body = {"locations": [[float(r["lon"]), float(r["lat"])] for r in chunk], "radius": RADIUS_M}
        for attempt in range(5):
            resp = requests.post(url, json=body, headers=headers, timeout=180)
            if resp.status_code == 200:
                break
            print(f"  HTTP {resp.status_code}, retrying...", flush=True)
            time.sleep(15 * (attempt + 1))
        resp.raise_for_status()
        for r, loc in zip(chunk, resp.json()["locations"]):
            if loc:
                r["road_lon"], r["road_lat"] = (f"{v:.6f}" for v in loc["location"])
                snapped += 1
            else:
                r["road_lat"] = r["road_lon"] = ""
        print(f"{min(start + BATCH, len(rows))}/{len(rows)} done", flush=True)
        time.sleep(2)

    fields = ["state", "name", "lat", "lon", "land_sqmi", "source", "road_lat", "road_lon"]
    with open(PLACES, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"snapped {snapped}/{len(rows)} ({100 * snapped / len(rows):.1f}%)")


if __name__ == "__main__":
    main()
