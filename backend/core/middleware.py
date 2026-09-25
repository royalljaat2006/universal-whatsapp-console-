"""HTTP middleware: correlation ids and access logging."""

import logging
import re
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from core.context import organization_id_var, request_id_var, user_id_var

logger = logging.getLogger("http.access")

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{8,128}$")
_UNLOGGED_PREFIXES = ("/health/", "/static/")


def _incoming_request_id(request: HttpRequest) -> str:
    """Accept an upstream id (nginx/load balancer) only if well-formed, so a
    client cannot inject arbitrary content into our logs."""
    candidate = request.headers.get(REQUEST_ID_HEADER, "")
    if candidate and _VALID_REQUEST_ID.match(candidate):
        return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Binds request_id for the whole request and echoes it on the response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = _incoming_request_id(request)
        request.request_id = request_id  # type: ignore[attr-defined]
        tokens = (
            request_id_var.set(request_id),
            organization_id_var.set(None),
            user_id_var.set(None),
        )
        try:
            response = self.get_response(request)
        finally:
            user_id_var.reset(tokens[2])
            organization_id_var.reset(tokens[1])
            request_id_var.reset(tokens[0])
        response[REQUEST_ID_HEADER] = request_id
        return response


class AccessLogMiddleware:
    """One structured line per request with latency. Runs after auth so the
    user id is known; organization id is bound later by tenant-scoped views."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            user_id_var.set(str(user.pk))

        started = time.perf_counter()
        response = self.get_response(request)
        if not request.path.startswith(_UNLOGGED_PREFIXES):
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            logger.log(
                level,
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        return response
