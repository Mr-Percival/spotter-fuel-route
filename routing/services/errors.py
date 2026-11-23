"""Domain errors, each mapped to an HTTP status by the API layer."""


class PlannerError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details

    def as_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **self.details}}


class LocationError(PlannerError):
    status_code = 400
    code = "invalid_location"


class AmbiguousLocationError(LocationError):
    code = "ambiguous_location"


class UnroutableLocationError(PlannerError):
    status_code = 422
    code = "unroutable_location"


class NoRouteError(PlannerError):
    status_code = 422
    code = "no_route"


class InfeasibleFuelPlanError(PlannerError):
    status_code = 422
    code = "fuel_gap"


class StationsNotLoadedError(PlannerError):
    status_code = 503
    code = "stations_not_loaded"


class RoutingServiceError(PlannerError):
    status_code = 502
    code = "routing_service_error"


class RoutingRateLimitError(RoutingServiceError):
    status_code = 429
    code = "routing_rate_limited"


class RoutingTimeoutError(RoutingServiceError):
    status_code = 504
    code = "routing_timeout"
