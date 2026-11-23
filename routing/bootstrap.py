"""Prepare an empty database at server start (serverless deployments).

On Vercel the SQLite file lives in /tmp and is empty on every cold start, so the
instance migrates and loads the committed station CSV before serving. Takes
about 1-2 s and runs once per instance.
"""

from django.core.management import call_command


def ensure_stations_loaded() -> None:
    from routing.models import Station

    call_command("migrate", interactive=False, verbosity=0)
    if not Station.objects.exists():
        call_command("load_stations", verbosity=0)
