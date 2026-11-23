from django.db import models


class Station(models.Model):
    """A truck stop with its (deduplicated) retail diesel price."""

    class GeoPrecision(models.TextChoices):
        ADDRESS = "address", "Street address"
        CITY = "city", "City centroid"

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=4, help_text="USD per gallon")
    latitude = models.FloatField()
    longitude = models.FloatField()
    geo_precision = models.CharField(max_length=10, choices=GeoPrecision.choices)

    class Meta:
        ordering = ["state", "city", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
