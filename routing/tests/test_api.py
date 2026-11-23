"""End-to-end API tests with the routing service mocked at the HTTP layer."""

from decimal import Decimal
from unittest import mock

import pytest
import requests
from django.core.cache import cache
from rest_framework.test import APIClient

from routing.models import Station
from routing.services.stations import reset_station_index

URL = "/api/route/"
# Oklahoma City -> Memphis, roughly along I-40 (~470 mi straight-line geometry).
ROUTE = [[-97.5164, 35.4676], [-94.4, 35.39], [-92.29, 34.75], [-90.049, 35.149]]


def ors_ok(distance_m=900_000.0, coords=ROUTE):
    resp = mock.Mock(status_code=200)
    resp.json.return_value = {"features": [{
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"summary": {"distance": distance_m, "duration": 30_000.0}},
    }]}
    return resp


@pytest.fixture(autouse=True)
def _setup(db, settings):
    settings.ORS_API_KEY = "test-key"
    settings.CORRIDOR_MILES = 10.0
    cache.clear()
    reset_station_index()
    for opis_id, lat, lon, price in [
        (1, 35.40, -96.0, 3.10),
        (2, 35.39, -94.4, 2.90),   # Fort Smith area, cheapest
        (3, 34.75, -92.29, 3.40),  # Little Rock
        (4, 35.15, -90.10, 3.20),  # Memphis
        (5, 40.00, -80.00, 1.00),  # far away: must be ignored
    ]:
        Station.objects.create(opis_id=opis_id, name=f"S{opis_id}", address="I-40", city="X",
                               state="OK", price=Decimal(str(price)), latitude=lat,
                               longitude=lon, geo_precision="city")
    yield
    reset_station_index()


@pytest.fixture
def client():
    return APIClient()


def test_plan_uses_exactly_one_routing_call(client):
    with mock.patch("routing.services.ors_client.requests.post", return_value=ors_ok()) as post:
        r = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN"}, format="json")
    assert r.status_code == 200, r.json()
    assert post.call_count == 1
    body = r.json()
    assert body["meta"]["routing_api_calls"] == 1
    assert body["distance_miles"] == pytest.approx(559.2, abs=0.1)
    assert body["route"]["geometry"]["coordinates"] == ROUTE
    assert all(s["opis_id"] != 5 for s in body["fuel_stops"])
    assert body["map_url"].startswith("/map/?start=")
    assert body["assumptions"]["stop_cost_usd"] == 25


def test_default_plan_pays_for_the_whole_trip(client):
    with mock.patch("routing.services.ors_client.requests.post", return_value=ors_ok()):
        body = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN"},
                           format="json").json()
    s = body["summary"]
    assert s["trip_fuel_gallons"] == pytest.approx(559.2 / 10, abs=0.05)
    # Fuel to reach the first station + fuel bought at the stops = all the trip's fuel.
    assert s["origin_fuel"]["gallons"] + s["gallons_bought_at_stops"] == pytest.approx(
        s["trip_fuel_gallons"], abs=0.05)
    assert s["total_cost"] == pytest.approx(
        s["origin_fuel"]["cost"] + sum(f["cost"] for f in body["fuel_stops"]), abs=0.05)
    assert s["total_cost"] > 0


def test_fuel_already_in_the_tank_is_not_charged(client):
    with mock.patch("routing.services.ors_client.requests.post", return_value=ors_ok()):
        body = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN",
                                 "initial_fuel_gallons": 50}, format="json").json()
    s = body["summary"]
    assert s["origin_fuel"]["cost"] == 0
    assert s["gallons_bought_at_stops"] == pytest.approx(559.2 / 10 - 50, abs=0.05)
    assert body["fuel_stops"][0]["opis_id"] == 2  # cheapest reachable


def test_short_trip_without_stations_on_route_and_map_render(client):
    short = [[-97.5164, 35.4676], [-97.30, 35.46]]
    with mock.patch("routing.services.ors_client.requests.post",
                    return_value=ors_ok(distance_m=20_000.0, coords=short)):
        body = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN"},
                           format="json").json()
        page = client.get("/map/", {"start": "Oklahoma City, OK", "finish": "Memphis, TN"})
    assert body["fuel_stops"] == [] and body["summary"]["total_cost"] > 0
    assert "nearest station" in body["summary"]["origin_fuel"]["priced_at"]
    assert page.status_code == 200


def test_stop_cost_parameter_is_echoed_and_cached_separately(client):
    payload = {"start": "Oklahoma City, OK", "finish": "Memphis, TN"}
    with mock.patch("routing.services.ors_client.requests.post", return_value=ors_ok()) as post:
        a = client.post(URL, payload, format="json").json()
        b = client.post(URL, {**payload, "stop_cost_usd": 0}, format="json").json()
    assert post.call_count == 2
    assert a["assumptions"]["stop_cost_usd"] == 25 and b["assumptions"]["stop_cost_usd"] == 0
    assert "stop_cost_usd=0" in b["map_url"]


def test_same_start_and_finish_is_400_without_routing(client):
    with mock.patch("routing.services.ors_client.requests.post") as post:
        r = client.post(URL, {"start": "Dallas, TX", "finish": "dallas, texas"}, format="json")
    assert r.status_code == 400
    post.assert_not_called()


def test_missing_station_data_is_503_without_routing(client):
    Station.objects.all().delete()
    reset_station_index()
    with mock.patch("routing.services.ors_client.requests.post") as post:
        r = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN"}, format="json")
    assert r.status_code == 503 and r.json()["error"]["code"] == "stations_not_loaded"
    post.assert_not_called()


def test_drf_errors_share_the_error_shape(client):
    r = client.generic("POST", URL, "{not json", content_type="application/json")
    assert r.status_code == 400 and "message" in r.json()["error"]
    r = client.put(URL, {}, format="json")
    assert r.status_code == 405 and r.json()["error"]["code"]


def test_map_url_on_cache_hit_reflects_current_request(client):
    with mock.patch("routing.services.ors_client.requests.post", return_value=ors_ok()):
        client.post(URL, {"start": "oklahoma city, ok", "finish": "Memphis, TN"}, format="json")
        r = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN"}, format="json")
    assert r.json()["meta"]["cache"] == "hit"
    assert "Oklahoma+City" in r.json()["map_url"]


def test_empty_tank_option_buys_everything(client):
    with mock.patch("routing.services.ors_client.requests.post", return_value=ors_ok()):
        r = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN",
                              "initial_fuel_gallons": 0}, format="json")
    # The first station is ~45 mi from the origin: an empty tank cannot reach it.
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "fuel_gap"
    assert r.json()["error"]["gap_start_mile"] == 0


def test_second_identical_request_is_served_from_cache(client):
    payload = {"start": "Oklahoma City, OK", "finish": "Memphis, TN"}
    with mock.patch("routing.services.ors_client.requests.post", return_value=ors_ok()) as post:
        client.post(URL, payload, format="json")
        r = client.post(URL, payload, format="json")
    assert post.call_count == 1
    assert r.json()["meta"]["cache"] == "hit"
    assert r.json()["meta"]["routing_api_calls"] == 0


def test_gap_longer_than_range_returns_422_with_location(client):
    Station.objects.filter(opis_id__in=[2, 3]).delete()
    reset_station_index()
    with mock.patch("routing.services.ors_client.requests.post",
                    return_value=ors_ok(distance_m=1_400_000.0)):
        r = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN"}, format="json")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "fuel_gap" and err["gap_end_mile"] > err["gap_start_mile"]


@pytest.mark.parametrize("payload, code", [
    ({"start": "Oklahoma City, OK"}, "invalid_request"),
    ({"start": "Springfield", "finish": "Memphis, TN"}, "ambiguous_location"),
    ({"start": "Oklahoma City, OK", "finish": "Memphis, TN", "initial_fuel_gallons": 80},
     "invalid_request"),
])
def test_bad_input_is_400(client, payload, code):
    with mock.patch("routing.services.ors_client.requests.post") as post:
        r = client.post(URL, payload, format="json")
    assert r.status_code == 400 and r.json()["error"]["code"] == code
    post.assert_not_called()


def test_alaska_is_422_without_calling_the_router(client):
    with mock.patch("routing.services.ors_client.requests.post") as post:
        r = client.post(URL, {"start": "Anchorage, AK", "finish": "Seattle, WA"}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "unroutable_location"
    post.assert_not_called()


@pytest.mark.parametrize("side_effect, status, code", [
    (requests.Timeout(), 504, "routing_timeout"),
    (requests.ConnectionError(), 502, "routing_service_error"),
])
def test_routing_failures_map_to_gateway_errors(client, side_effect, status, code):
    with mock.patch("routing.services.ors_client.requests.post", side_effect=side_effect):
        r = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN"}, format="json")
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_ors_unroutable_point_is_422(client):
    resp = mock.Mock(status_code=404)
    resp.json.return_value = {"error": {"code": 2010, "message": "Could not find routable point"}}
    with mock.patch("routing.services.ors_client.requests.post", return_value=resp):
        r = client.post(URL, {"start": "Oklahoma City, OK", "finish": "Memphis, TN"}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_route"


def test_map_page_renders(client):
    with mock.patch("routing.services.ors_client.requests.post", return_value=ors_ok()):
        r = client.get("/map/", {"start": "Oklahoma City, OK", "finish": "Memphis, TN"})
    assert r.status_code == 200
    assert b'id="plan"' in r.content and b"leaflet" in r.content
