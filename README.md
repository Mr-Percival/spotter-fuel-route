# Fuel Route Planner

A Django REST API that takes a start and a finish location in the USA and returns:

- the driving route (GeoJSON) and a link to an interactive map,
- the **cost-optimal** fuel stops along it (500-mile range, 10 mpg, prices from the provided truck-stop list),
- the total money spent on fuel.

It makes **one call** to the routing API per trip (OpenRouteService). Repeated trips are served from cache.

```
POST /api/route/   {"start": "New York, NY", "finish": "Los Angeles, CA"}

→ 2,793 mi · 10 stops · $676.19 · $82.21 cheaper than refuelling only when forced
  routing 2.1 s (OpenRouteService) + 40 ms of our own work; cache hit: 9 ms
```

![map](docs/map.jpg)

## Quick start

Requires Python 3.12+ and a free [OpenRouteService key](https://openrouteservice.org/dev/#/signup).

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # then set ORS_API_KEY
python manage.py migrate
python manage.py load_stations                        # loads the committed, geocoded stations
python manage.py runserver
```

Then:

- **API:** `POST http://127.0.0.1:8000/api/route/`. The Postman collection is in `postman_collection.json`.
- **Map:** http://127.0.0.1:8000/map/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA
- **Tests:** `pytest` runs 540+ tests in about 3 s, with the routing API mocked.

## API

`POST /api/route/` (GET with the same query parameters also works)

| field | type | |
|---|---|---|
| `start`, `finish` | string | `"City, ST"`, `"City, State"` or `"lat,lon"` (contiguous US) |
| `initial_fuel_gallons` | number, optional | 0–50, default **50** (full tank) |

<details><summary>Response (trimmed)</summary>

```json
{
  "origin": {"query": "New York, NY", "resolved": "New York, NY", "lat": 40.6631, "lon": -73.9387},
  "destination": {"query": "Los Angeles, CA", "resolved": "Los Angeles, CA", "lat": 34.0196, "lon": -118.4104},
  "distance_miles": 2793.1,
  "duration_hours": 53.6,
  "assumptions": {"tank_gallons": 50, "mpg": 10, "max_range_miles": 500,
                  "initial_fuel_gallons": 50, "corridor_miles": 10,
                  "price_rule": "lowest retail price per OPIS station id", "...": "..."},
  "summary": {"total_cost": 676.19, "gallons_purchased": 229.31, "stops": 10,
              "avg_price_paid": 2.949, "savings_vs_naive": 82.21},
  "fuel_stops": [
    {"sequence": 1, "opis_id": 72781, "name": "SHEETZ #774", "address": "I-70 & SR-83",
     "city": "New Concord", "state": "OH", "mile": 471.3, "off_route_miles": 0.9,
     "price_per_gallon": 3.059, "gallons": 6.52, "cost": 19.95,
     "lat": 39.993658, "lon": -81.738064, "geo_precision": "city"}
  ],
  "route": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-73.94, 40.66], "..."]}},
  "map_url": "/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "meta": {"routing_api_calls": 1, "routing_profile": "driving-hgv", "stations_in_corridor": 403,
           "cache": "miss", "timings_ms": {"geocode": 0.0, "routing": 2092.4, "corridor": 37.6,
                                            "optimize": 0.6, "total": 2130.8}},
  "warnings": ["Some stops are located at their city's centroid because the dataset lists highway exits rather than street addresses."]
}
```
</details>

**Errors** share one shape, `{"error": {"code", "message", ...}}`:

| status | code | example |
|---|---|---|
| 400 | `invalid_request` | missing field, `initial_fuel_gallons` > 50 |
| 400 | `ambiguous_location` | `"Springfield"`: lists `Springfield, IL`, `Springfield, MO`, … |
| 400 | `invalid_location` | unknown city/state |
| 422 | `unroutable_location` | `"Anchorage, AK"` (road access only through Canada), `"Honolulu, HI"` |
| 422 | `fuel_gap` | `San Francisco → Seattle`: *no station between mile 0 and 548* |
| 422 | `no_route` | the routing engine finds no road |
| 429 / 502 / 504 | `routing_*` | routing API rate limit, error or timeout |

## How it works

```
start, finish ──► local geocoder (Census) ──► 1 × OpenRouteService directions (driving-hgv)
                                                         │ route polyline + distance
     6,464 stations (in memory, KD-tree) ──► corridor search ──► stations with mile markers
                                                         │
                                         greedy optimiser ──► stops, gallons, cost
```

1. **Geocoding without API calls.** `"City, ST"` is resolved against the US Census Gazetteer (cities, towns, CDPs and townships). Each place was pre-snapped once to the nearest truck-routable road (`scripts/snap_places.py`). Census "internal points" can sit off-road: San Francisco's is in the Pacific because the city includes the Farallon Islands.
2. **One routing call.** It goes to OpenRouteService with `avoid_borders: "all"`, so US→US trips stay in the US. Without it, Detroit → Buffalo would cut through Ontario.
3. **Corridor search.**
   - The route is densified to 1-mile steps.
   - A KD-tree over 3-D unit vectors finds, for every station in a single vectorised query, its nearest route point within 10 miles. That point's cumulative distance becomes the station's mile marker.
   - This costs O(S log P) and takes about 40 ms for a coast-to-coast route, with no PostGIS needed.
4. **Optimal refuelling.** `routing/services/fuel_optimizer.py` is pure Python with no Django imports. It applies the classic greedy for this problem. At each station:
   - If a cheaper station is within one tank, it buys just enough to reach the first such station.
   - Otherwise, if the destination is in range, it buys just enough to finish.
   - Otherwise it fills up and drives to the cheapest station in range.

   This is optimal for linear prices with free stops. The proof is an exchange argument, in the module docstring. Instead of trusting it, **`test_fuel_optimizer.py` checks it against an exact dynamic programme on 400 random instances**, feasible and infeasible ones, and replays every plan to verify the tank never overflows or runs dry.
5. **Caching and warm-up.** Results are cached per (origin, destination, initial fuel). The station and place indexes are built at server start (`config/wsgi.py`), not per request.

## Data pipeline (offline, results committed)

The provided CSV has no coordinates, and most addresses are highway exits ("I-44, EXIT 283 & US-69").

`python manage.py geocode_stations [--census]` does the following:

| step | result |
|---|---|
| keep the 50 states + DC (drops Canadian provinces) | 8,151 → 7,531 rows |
| one row per OPIS id, keeping the **lowest** price (905 rows repeated an id with different prices) | 6,626 stations |
| Census Bureau batch geocoder on the street address, accepted only within 25 mi of the city | 477 (7.4%) |
| otherwise the Census Gazetteer centroid of the city/town | 5,987 (92.6%) |
| unresolved (unincorporated places in neither source) | 162 dropped (2.4%) |
| fix mojibake (`Stuckeyâ€™s` → `Stuckey’s`) | ✓ |

Total: **6,464 stations (97.6%)** loaded into the `Station` model.

## Assumptions and trade-offs

- **The truck leaves with a full tank.** The spec doesn't say, and that is the usual case for a truck leaving a terminal. `total_cost` is the fuel bought along the way. Pass `initial_fuel_gallons: 0` to model an empty start; the API then reports a `fuel_gap` if no station is within reach of the origin.
- **Stations within 10 miles of the route count as on-route**, and the detour cost is ignored. Most stations are placed at their city's centroid, so a tighter corridor would miss real highway stops. The corridor width is configurable with `CORRIDOR_MILES`.
- **Duplicate ids use the lowest price**, as a dispatcher would pick the cheapest quote.
- **Dataset gaps are reported, not invented.** California has only 16 stations, all in the Imperial Valley. So San Francisco → Seattle has no station for its first 548 miles, which exceeds the 500-mile range, and the API says so.
- **Not built, on purpose:** auth, PostGIS, async workers. The spatial work runs in memory in milliseconds for about 6.5k stations. With more data or many instances, it would move to PostGIS (`ST_DWithin` on the route line) and a shared cache (Redis).

## Project layout

```
config/                     settings (env-driven), urls, wsgi warm-up
routing/
  models.py                 Station
  views.py, serializers.py  DRF endpoint + map page
  services/
    planner.py              orchestration, caching, response shape
    ors_client.py           the single routing call + error mapping
    geocode.py, places.py   local geocoding (Census Gazetteer)
    corridor.py, stations.py KD-tree corridor search
    fuel_optimizer.py       greedy optimiser (pure Python)
  management/commands/      geocode_stations, load_stations
  tests/                    optimiser vs exact DP, API (routing mocked), geocoding, corridor, data
scripts/                    one-off data preparation (Gazetteer build, road snapping)
data/                       assessment CSV + derived, committed data
```

Data sources: the assessment's fuel price CSV and the US Census Bureau (Gazetteer 2025, Geocoder; public domain). Routing and road snapping by © openrouteservice.org / OpenStreetMap contributors.
