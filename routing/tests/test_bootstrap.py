import pytest

from routing.bootstrap import ensure_stations_loaded
from routing.models import Station


@pytest.mark.django_db
def test_bootstrap_loads_stations_once():
    assert not Station.objects.exists()
    ensure_stations_loaded()
    n = Station.objects.count()
    assert n > 6000
    ensure_stations_loaded()  # idempotent: does not reload or duplicate
    assert Station.objects.count() == n
