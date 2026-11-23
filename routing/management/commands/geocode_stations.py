"""Offline, one-time: clean the assessment CSV and give every station coordinates.

    python manage.py geocode_stations            # uses the cached Census results
    python manage.py geocode_stations --census   # (re)queries the Census batch geocoder

Pipeline
1. Fix mojibake, keep the 50 states + DC (drops Canadian provinces).
2. One row per OPIS id, keeping the **lowest** retail price (the CSV repeats
   678 ids with different prices; a dispatcher would pick the cheapest quote).
3. Coordinates, best first:
   a. Census Bureau batch geocoder on the street address, accepted only if it
      lands within 25 mi of the city's centroid (guards against wrong matches);
   b. otherwise the centroid of the city/town (Census Gazetteer).
   Rows with neither are reported and dropped.
The result, data/stations_geocoded.csv, is committed so the API never needs
to geocode stations at request time.
"""

from __future__ import annotations

import csv
import io
import math
from collections import Counter
from pathlib import Path

import requests
from django.core.management.base import BaseCommand

from routing.services.places import DATA_DIR, US_STATES, lookup

RAW_CSV = DATA_DIR / "fuel-prices-for-be-assessment.csv"
CENSUS_CACHE = DATA_DIR / "census_geocoder_results.csv"
OUTPUT_CSV = DATA_DIR / "stations_geocoded.csv"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
MAX_ADDRESS_TO_CITY_MILES = 25.0


def fix_text(s: str) -> str:
    s = s.strip()
    if "â" in s or "Ã" in s:
        try:
            return s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def load_clean_rows() -> tuple[list[dict], Counter]:
    stats: Counter = Counter()
    best: dict[str, dict] = {}
    with open(RAW_CSV, encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            r = {k.strip(): fix_text(v) for k, v in raw.items()}
            stats["raw_rows"] += 1
            if r["State"] not in US_STATES:
                stats["dropped_non_us"] += 1
                continue
            row = {
                "opis_id": r["OPIS Truckstop ID"],
                "name": r["Truckstop Name"],
                "address": r["Address"],
                "city": r["City"],
                "state": r["State"],
                "rack_id": r["Rack ID"],
                "price": float(r["Retail Price"]),
            }
            prev = best.get(row["opis_id"])
            if prev is None:
                best[row["opis_id"]] = row
            else:
                stats["duplicate_rows_merged"] += 1
                if row["price"] < prev["price"]:
                    best[row["opis_id"]] = row
    stats["unique_us_stations"] = len(best)
    return list(best.values()), stats


def query_census(rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow([r["opis_id"], r["address"], r["city"], r["state"], ""])
    resp = requests.post(
        CENSUS_URL,
        files={"addressFile": ("stations.csv", buf.getvalue(), "text/csv")},
        data={"benchmark": "Public_AR_Current"},
        timeout=900,
    )
    resp.raise_for_status()
    CENSUS_CACHE.write_text(resp.text, encoding="utf-8")


def read_census() -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    if not CENSUS_CACHE.exists():
        return out
    with open(CENSUS_CACHE, encoding="utf-8") as f:
        for rec in csv.reader(f):
            # id, input, Match|No_Match|Tie, Exact|Non_Exact, matched address, "lon,lat", ...
            if len(rec) >= 6 and rec[2] == "Match" and rec[5]:
                lon, lat = (float(x) for x in rec[5].split(","))
                out[rec[0]] = (lat, lon)
    return out


class Command(BaseCommand):
    help = "Clean the fuel price CSV and geocode every US station (offline, run once)."

    def add_arguments(self, parser):
        parser.add_argument("--census", action="store_true",
                            help="Query the Census batch geocoder (otherwise use the cache).")

    def handle(self, *args, **opts):
        rows, stats = load_clean_rows()
        if opts["census"]:
            self.stdout.write(f"Querying Census batch geocoder for {len(rows)} addresses...")
            query_census(rows)
        census = read_census()

        out = []
        for r in rows:
            city = lookup(r["city"], r["state"])
            point = census.get(r["opis_id"])
            if point and city and haversine_miles(*point, city.lat, city.lon) > MAX_ADDRESS_TO_CITY_MILES:
                stats["address_match_rejected_far_from_city"] += 1
                point = None
            if point:
                lat, lon, precision = point[0], point[1], "address"
            elif city:
                lat, lon, precision = city.lat, city.lon, "city"
            else:
                stats["dropped_no_coordinates"] += 1
                continue
            stats[f"precision_{precision}"] += 1
            out.append({**r, "lat": round(lat, 6), "lon": round(lon, 6), "geo_precision": precision})

        out.sort(key=lambda r: (r["state"], r["city"], r["opis_id"]))
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)

        total = len(out)
        self.stdout.write(self.style.SUCCESS(f"Wrote {total} stations to {OUTPUT_CSV.name}"))
        for k, v in sorted(stats.items()):
            pct = f" ({100 * v / total:.1f}% of output)" if k.startswith("precision_") else ""
            self.stdout.write(f"  {k}: {v}{pct}")
