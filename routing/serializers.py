from django.conf import settings
from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=120, help_text="'City, ST' or 'lat,lon' in the US")
    finish = serializers.CharField(max_length=120, help_text="'City, ST' or 'lat,lon' in the US")
    initial_fuel_gallons = serializers.FloatField(
        required=False,
        min_value=0,
        max_value=settings.VEHICLE_TANK_GALLONS,
        help_text="Fuel in the tank at departure. Defaults to a full tank.",
    )
