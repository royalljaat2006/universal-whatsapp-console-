"""HTTP middleware: correlation ids, access logging, and row-level security."""

import logging
import re
import time
import uuid
from collections.abc import Callable

from django.db import connection, transaction
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
            duration_s = time.perf_counter() - started
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            logger.log(
                level,
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_s * 1000, 2),
                },
            )
            # URL pattern, not the raw path, so ids never explode metric
            # cardinality (e.g. /api/v1/contacts/<uuid>/ for every contact).
            resolver_match = getattr(request, "resolver_match", None)
            path_template = getattr(resolver_match, "route", None) or request.path
            from core.metrics import HTTP_REQUEST_DURATION_SECONDS

            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=request.method, path_template=path_template, status=response.status_code
            ).observe(duration_s)
        return response


RLS_ROLE = "whatsapp_platform_app"  # keep in sync with core/migrations/0001_row_level_security.py


def set_rls_organization(organization_id: object) -> None:
    """Switches the current transaction to the restricted `whatsapp_platform_app`
    role and sets the session variable core/migrations/0001_row_level_security.py's
    policies check. Both are done with SET LOCAL — scoped to the current
    transaction only, so neither can leak to the next request on a pooled
    connection (CONN_MAX_AGE), and both revert automatically to the owning
    role when the transaction ends, success or not.

    The connection otherwise always authenticates as the table-owning role,
    which Postgres lets bypass RLS unconditionally — so migrations, webhook
    ingestion, Django admin and background workers are completely unaffected
    (they never call this function, so RLS never engages for them, exactly
    matching apps.organizations.tenancy's documented "system context" carve-out).
    Only requests that resolve a tenant context explicitly opt into the
    restricted role. Call only from inside RowLevelSecurityMiddleware's
    transaction (i.e. from apps.organizations.api.scoping.OrganizationScopedMixin,
    once the membership's organization is resolved). A no-op on non-Postgres
    connections (e.g. sqlite in some test configurations)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL ROLE {RLS_ROLE}")
        cursor.execute("SELECT set_config('app.current_org_id', %s, true)", [str(organization_id)])


class RowLevelSecurityMiddleware:
    """Wraps tenant API requests (``/api/v1/...``) in one transaction so
    OrganizationScopedMixin can SET LOCAL the restricted role and tenant
    context (set_rls_organization) for Postgres RLS policies to see — see
    core/migrations/0001_row_level_security.py for why this is defense in
    depth, not the primary isolation mechanism (that stays
    apps.organizations.api.scoping.OrganizationScopedMixin /
    TenantScopedModel.objects.for_organization()). Runs last (closest to the
    view) so it wraps only the view's own DB work, not session/CSRF/auth
    middleware.

    Scoped to ``/api/v1/`` specifically (not webhooks, health, admin, or
    static/media) because those paths never call set_rls_organization at
    all — webhook ingestion in particular needs unscoped, cross-tenant
    lookups by provider id (see apps.whatsapp.account_events), which the
    restricted role could never satisfy since no org context is ever set
    for that request. Wrapping them in an extra transaction would only add
    latency for no benefit."""

    TENANT_API_PREFIX = "/api/v1/"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith(self.TENANT_API_PREFIX):
            return self.get_response(request)
        with transaction.atomic():
            return self.get_response(request)
