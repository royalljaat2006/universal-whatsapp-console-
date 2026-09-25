"""Individual dependency probes. Each returns a CheckResult and never raises."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor


@dataclass
class CheckResult:
    name: str
    ok: bool
    duration_ms: float
    detail: str = ""
    critical: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "critical": self.critical,
            "duration_ms": self.duration_ms,
        }
        if self.detail:
            data["detail"] = self.detail
        data.update(self.extra)
        return data


def _timed(name: str, fn: Callable[[], tuple[bool, str]], *, critical: bool = True) -> CheckResult:
    started = time.perf_counter()
    try:
        ok, detail = fn()
    except Exception as exc:  # a probe must report, not crash
        ok, detail = False, type(exc).__name__
    return CheckResult(
        name=name,
        ok=ok,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        detail=detail,
        critical=critical,
    )


def check_database() -> CheckResult:
    def probe() -> tuple[bool, str]:
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, ""

    return _timed("database", probe)


def check_migrations() -> CheckResult:
    """Not ready while migrations are pending: code and schema disagree."""

    def probe() -> tuple[bool, str]:
        executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return (not pending, f"{len(pending)} pending" if pending else "")

    return _timed("migrations", probe)


def check_cache() -> CheckResult:
    def probe() -> tuple[bool, str]:
        cache.set("health:probe", "1", timeout=5)
        return cache.get("health:probe") == "1", ""

    return _timed("redis_cache", probe)


def check_celery_workers() -> CheckResult:
    """Informational: web readiness must not depend on worker count."""

    def probe() -> tuple[bool, str]:
        from config.celery import app

        replies = app.control.ping(timeout=1.0) or []
        return bool(replies), f"{len(replies)} worker(s) responded"

    return _timed("celery_workers", probe, critical=False)


def check_meta_configuration() -> CheckResult:
    """Configuration presence only. Live connectivity is verified per WABA by
    the provider health check (it needs a tenant credential)."""

    def probe() -> tuple[bool, str]:
        missing = [
            name
            for name in ("META_APP_SECRET", "META_WEBHOOK_VERIFY_TOKEN")
            if not getattr(settings, name)
        ]
        if missing:
            return False, "CONFIGURATION REQUIRED: " + ", ".join(missing)
        return True, f"graph {settings.META_GRAPH_API_VERSION}"

    return _timed("meta_configuration", probe, critical=False)
