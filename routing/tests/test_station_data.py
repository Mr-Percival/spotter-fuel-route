"""The committed station dataset meets HU1's acceptance criteria."""

import csv
from collections import Counter

from routing.management.commands.geocode_stations import OUTPUT_CSV, RAW_CSV
from routing.services.places import US_STATES


def _rows(path):
    with open(path, encoding="utf-8") as f:
        return [{k.strip(): v for k, v in r.items()} for r in csv.DictReader(f)]


def test_only_us_stations_and_unique_ids():
    rows = _rows(OUTPUT_CSV)
    assert {r["state"] for r in rows} <= US_STATES
    assert max(Counter(r["opis_id"] for r in rows).values()) == 1


def test_more_than_95_percent_of_us_stations_have_coordinates():
    raw_ids = {r["OPIS Truckstop ID"].strip() for r in _rows(RAW_CSV)
               if r["State"].strip() in US_STATES}
    out = _rows(OUTPUT_CSV)
    assert len(out) / len(raw_ids) > 0.95
    assert all(r["lat"] and r["lon"] for r in out)


def test_duplicates_keep_the_lowest_price():
    raw = _rows(RAW_CSV)
    lowest = {}
    for r in raw:
        i, p = r["OPIS Truckstop ID"].strip(), float(r["Retail Price"])
        lowest[i] = min(p, lowest.get(i, p))
    for r in _rows(OUTPUT_CSV):
        assert float(r["price"]) == lowest[r["opis_id"]]


def test_mojibake_is_fixed():
    assert not any("â€" in r["name"] for r in _rows(OUTPUT_CSV))
