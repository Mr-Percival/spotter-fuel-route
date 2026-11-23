from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler

from .serializers import RouteRequestSerializer
from .services.errors import PlannerError
from .services.planner import plan_trip

_OPTIONAL = ("initial_fuel_gallons", "stop_cost_usd")


def _map_url(validated: dict) -> str:
    params = {k: validated[k] for k in ("start", "finish", *_OPTIONAL) if validated.get(k) is not None}
    return f"{reverse('route-map')}?{urlencode(params)}"


def _run(validated: dict) -> dict:
    result = plan_trip(validated["start"], validated["finish"],
                       validated.get("initial_fuel_gallons"), validated.get("stop_cost_usd"))
    return {**result, "map_url": _map_url(validated)}


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
            return Response({"error": {"code": "invalid_request",
                                       "message": "Some fields are missing or invalid.",
                                       "fields": serializer.errors}}, status=400)
        try:
            return Response(_run(serializer.validated_data))
        except PlannerError as exc:
            return Response(exc.as_dict(), status=exc.status_code)


def api_exception_handler(exc, context):
    """Give DRF's own errors (bad JSON, 405, throttling) the same shape as ours."""
    response = exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and "detail" in response.data:
        code = getattr(response.data["detail"], "code", "error")
        response.data = {"error": {"code": str(code), "message": str(response.data["detail"])}}
    return response


def route_map(request):
    """Leaflet map of the planned route and stops (same planner, same cache)."""
    context = {"result": None, "error": None, "form": request.GET}
    if request.GET.get("start") or request.GET.get("finish"):
        serializer = RouteRequestSerializer(data=request.GET)
        if not serializer.is_valid():
            context["error"] = "Enter a start and a finish as 'City, ST' (e.g. 'Dallas, TX')."
        else:
            try:
                context["result"] = _run(serializer.validated_data)
            except PlannerError as exc:
                context["error"] = exc.message
    status = 400 if context["error"] else 200
    return render(request, "routing/map.html", context, status=status)
