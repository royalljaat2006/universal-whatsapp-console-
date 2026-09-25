"""Health endpoints.

- ``/health/live``  — process is up. No dependency calls (a DB outage must
  not make the orchestrator kill healthy processes).
- ``/health/ready`` — can serve traffic: database, pending migrations, Redis.
- ``/health/dependencies`` — staff-only extended report (Celery workers,
  Meta configuration). Non-critical checks never flip readiness.
"""

from django.http import HttpRequest, JsonResponse
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
