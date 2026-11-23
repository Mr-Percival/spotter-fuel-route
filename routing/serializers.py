from django.conf import settings
from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=120, help_text="'City, ST' or 'lat,lon' in the US")
    finish = serializers.CharField(max_length=120, help_text="'City, ST' or 'lat,lon' in the US")
    initial_fuel_gallons = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=settings.VEHICLE_TANK_GALLONS,
        help_text="Fuel already in the tank (not charged). Omit to charge the whole trip.",
    )
    stop_cost_usd = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=1000,
        help_text="Fixed cost of each fuel stop. Defaults to STOP_COST_USD (25).",
    )
