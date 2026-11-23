from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.errors import PlannerError
from .services.planner import plan_trip


class RoutePlanView(APIView):
    """Plan the cheapest fuel stops between two US locations.

    POST {"start": "New York, NY", "finish": "Los Angeles, CA"}
    (GET with the same fields as query parameters also works.)
    """

    def post(self, request):
        return self._plan(request.data)

    def get(self, request):
        return self._plan(request.query_params)

    def _plan(self, data):
        serializer = RouteRequestSerializer(data=data)
        if not serializer.is_valid():
            return Response({"error": {"code": "invalid_request", "fields": serializer.errors}},
                            status=400)
        v = serializer.validated_data
        try:
            result = plan_trip(v["start"], v["finish"], v.get("initial_fuel_gallons"))
        except PlannerError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        return Response(result)


def route_map(request):
    """Leaflet map of the planned route and stops (same planner, same cache)."""
    serializer = RouteRequestSerializer(data=request.GET)
    context = {"result": None, "error": None,
               "start": request.GET.get("start", ""), "finish": request.GET.get("finish", "")}
    if not serializer.is_valid():
        context["error"] = "Provide ?start=City, ST&finish=City, ST"
    else:
        v = serializer.validated_data
        try:
            context["result"] = plan_trip(v["start"], v["finish"], v.get("initial_fuel_gallons"))
        except PlannerError as exc:
            context["error"] = exc.message
    status = 200 if context["error"] is None else 400
    return render(request, "routing/map.html", context, status=status)
