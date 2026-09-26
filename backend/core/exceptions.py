"""Domain exceptions and the single API error contract.

Every API error response has the shape::

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}

Domain/service code raises ``DomainError`` subclasses; views never build
error payloads by hand. Stack traces are logged, never returned.
"""

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.response import Response

from core.context import request_id_var

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Base for expected, client-meaningful failures."""

    code: str = "DOMAIN_ERROR"
    status_code: int = status.HTTP_400_BAD_REQUEST
    default_message: str = "The request could not be completed."

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND
    default_message = "Resource not found."


class ConflictError(DomainError):
    code = "CONFLICT"
    status_code = status.HTTP_409_CONFLICT
    default_message = "The request conflicts with the current state."


class OrganizationRequiredError(DomainError):
    code = "ORGANIZATION_REQUIRED"
    default_message = "The X-Organization-ID header is required for this endpoint."


class ConfigurationRequiredError(DomainError):
    code = "CONFIGURATION_REQUIRED"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_message = "This capability is not configured."


class EmbeddedSignupError(DomainError):
    """The Embedded Signup code exchange with Meta failed — e.g. the code
    expired (it is valid for only ~30 seconds) or was already used."""

    code = "EMBEDDED_SIGNUP_FAILED"
    status_code = status.HTTP_502_BAD_GATEWAY
    default_message = "Could not complete WhatsApp Embedded Signup."


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id_var.get(),
        }
    }


_DRF_CODE_MAP: dict[type[drf_exceptions.APIException], str] = {
    drf_exceptions.ValidationError: "VALIDATION_ERROR",
    drf_exceptions.ParseError: "MALFORMED_REQUEST",
    drf_exceptions.NotAuthenticated: "AUTHENTICATION_REQUIRED",
    drf_exceptions.AuthenticationFailed: "AUTHENTICATION_FAILED",
    drf_exceptions.PermissionDenied: "PERMISSION_DENIED",
    drf_exceptions.NotFound: "NOT_FOUND",
    drf_exceptions.MethodNotAllowed: "METHOD_NOT_ALLOWED",
    drf_exceptions.NotAcceptable: "NOT_ACCEPTABLE",
    drf_exceptions.UnsupportedMediaType: "UNSUPPORTED_MEDIA_TYPE",
    drf_exceptions.Throttled: "RATE_LIMITED",
}


def _drf_code(exc: drf_exceptions.APIException) -> str:
    for cls in type(exc).__mro__:
        if cls in _DRF_CODE_MAP:
            return _DRF_CODE_MAP[cls]
    return "API_ERROR"


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    if isinstance(exc, DomainError):
        return Response(error_body(exc.code, exc.message, exc.details), status=exc.status_code)

    if isinstance(exc, Http404):
        exc = drf_exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = drf_exceptions.PermissionDenied()
    elif isinstance(exc, DjangoValidationError):
        # e.g. a malformed UUID in a query-string filter.
        exc = drf_exceptions.ValidationError(exc.messages)

    if isinstance(exc, drf_exceptions.APIException):
        code = _drf_code(exc)
        if isinstance(exc, drf_exceptions.ValidationError):
            details = {"fields": exc.detail}
            message = "Request validation failed."
        else:
            details = {}
            message = str(exc.detail)
        headers = {}
        wait = getattr(exc, "wait", None)
        if isinstance(exc, drf_exceptions.Throttled) and wait:
            headers["Retry-After"] = str(int(wait))
        # 401 vs 403: session auth has no WWW-Authenticate challenge, so DRF
        # would downgrade to 403; keep the semantic status explicit.
        status_code = exc.status_code
        if code == "AUTHENTICATION_REQUIRED":
            status_code = status.HTTP_401_UNAUTHORIZED
        return Response(error_body(code, message, details), status=status_code, headers=headers)

    from core.observability import capture_exception

    capture_exception(exc, source="api", view=type(context.get("view")).__name__)
    return Response(
        error_body("INTERNAL_ERROR", "An unexpected error occurred."),
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
