"""Health endpoints.

- ``/health/live``  — process is up. No dependency calls (a DB outage must
  not make the orchestrator kill healthy processes).
- ``/health/ready`` — can serve traffic: database, pending migrations, Redis.
- ``/health/dependencies`` — staff-only extended report (Celery workers,
  Meta configuration). Non-critical checks never flip readiness.
- ``/metrics`` — Prometheus exposition format, gated by ``METRICS_TOKEN``
  (see core.metrics). Undocumented for staff use; it is a scrape target for
  an operator's own Prometheus, not a page anyone browses to.
"""

import hmac

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core.health.checks import (
    CheckResult,
    check_cache,
    check_celery_workers,
    check_database,
    check_meta_configuration,
    check_migrations,
)


def _report(results: list[CheckResult]) -> JsonResponse:
    ready = all(r.ok for r in results if r.critical)
    return JsonResponse(
        {
            "status": "ok" if ready else "unavailable",
            "checks": {r.name: r.as_dict() for r in results},
        },
        status=200 if ready else 503,
    )


@never_cache
@require_GET
def live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def ready(request: HttpRequest) -> JsonResponse:
    return _report([check_database(), check_migrations(), check_cache()])


@never_cache
@require_GET
def dependencies(request: HttpRequest) -> JsonResponse:
    if not (request.user.is_authenticated and request.user.is_staff):
        return JsonResponse({"status": "forbidden"}, status=403)
    return _report(
        [
            check_database(),
            check_migrations(),
            check_cache(),
            check_celery_workers(),
            check_meta_configuration(),
        ]
    )


@never_cache
@require_GET
def metrics(request: HttpRequest) -> HttpResponse:
    """Fails closed: unset METRICS_TOKEN (the default) means this endpoint
    is not reachable at all, not reachable-without-auth. Set it only once an
    operator's own Prometheus is ready to scrape (see
    infrastructure/monitoring/README.md)."""
    token = getattr(settings, "METRICS_TOKEN", "")
    if not token:
        return HttpResponse(status=404)
    provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not provided or not hmac.compare_digest(provided, token):
        return HttpResponse(status=403)

    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from core.metrics import REGISTRY, refresh_gauges

    refresh_gauges()
    return HttpResponse(generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)
