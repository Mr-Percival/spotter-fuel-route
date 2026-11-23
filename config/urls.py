from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from routing.views import RoutePlanView, route_map

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/route/", RoutePlanView.as_view(), name="route-plan"),
    path("map/", route_map, name="route-map"),
    path("", RedirectView.as_view(url="/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA")),
]
