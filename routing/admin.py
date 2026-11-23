from django.contrib import admin

from .models import Station


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "price", "geo_precision")
    list_filter = ("state", "geo_precision")
    search_fields = ("name", "city", "opis_id")
