"""Load data/stations_geocoded.csv into the Station table (idempotent).

    python manage.py load_stations
"""

import csv
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from routing.management.commands.geocode_stations import OUTPUT_CSV
from routing.models import Station


class Command(BaseCommand):
    help = "Load geocoded stations into the database (replaces existing rows)."

    def handle(self, *args, **opts):
        with open(OUTPUT_CSV, encoding="utf-8") as f:
            objs = [
                Station(
                    opis_id=int(r["opis_id"]),
                    name=r["name"],
                    address=r["address"],
                    city=r["city"],
                    state=r["state"],
                    rack_id=int(r["rack_id"]) if r["rack_id"] else None,
                    price=Decimal(r["price"]),
                    latitude=float(r["lat"]),
                    longitude=float(r["lon"]),
                    geo_precision=r["geo_precision"],
                )
                for r in csv.DictReader(f)
            ]
        with transaction.atomic():
            Station.objects.all().delete()
            Station.objects.bulk_create(objs, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(objs)} stations."))
