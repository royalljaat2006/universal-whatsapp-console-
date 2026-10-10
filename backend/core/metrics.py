"""Prometheus metrics — the phase-2 monitoring increment proposed in
infrastructure/monitoring/README.md. Turns the signals that document already
described as SQL/log-based ("Signals to alert on now") into a scrapable
``/metrics`` endpoint, using the existing dependencies (Redis, the DB) rather
than adding a new service. No Prometheus server, Grafana or Sentry is
deployed here — an operator's own self-hosted Prometheus scrapes this
endpoint; infrastructure/monitoring/alerts.yml has ready-to-load alert rules
for it.

Request-latency instrumentation lives in core.middleware.AccessLogMiddleware
(it already times every request for the structured log line) rather than a
second middleware duplicating that timing."""

import logging
from django.db.models import Count

from prometheus_client import CollectorRegistry, Gauge, Histogram

REGISTRY = CollectorRegistry()

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by method, URL pattern and status code.",
    ["method", "path_template", "status"],
    registry=REGISTRY,
)

CELERY_QUEUE_DEPTH = Gauge(
    "celery_queue_depth",
    "Pending tasks waiting in each Celery/Redis broker queue.",
    ["queue"],
    registry=REGISTRY,
)

CELERY_WORKERS_ONLINE = Gauge(
    "celery_workers_online",
    "Celery workers that responded to a control ping just now.",
    registry=REGISTRY,
)

DEPENDENCY_UP = Gauge(
    "dependency_up",
    "1 if the dependency answered its health probe just now, else 0.",
    ["dependency"],
    registry=REGISTRY,
)

WEBHOOK_EVENTS_BY_STATUS = Gauge(
    "webhook_events_by_status",
    "Current count of webhooks_webhookevent rows by status.",
    ["status"],
    registry=REGISTRY,
)

MESSAGES_FAILED_BY_ERROR_CODE = Gauge(
    "messages_failed_by_error_code",
    "Current count of failed outbound messages in the last 24h, by error code.",
    ["error_code"],
    registry=REGISTRY,
)

BACKUP_LAST_SUCCESS_AGE_SECONDS = Gauge(
    "backup_last_success_age_seconds",
    "Seconds since the last successful database backup (see core.BackupRecord).",
    registry=REGISTRY,
)

BACKUP_LAST_STATUS = Gauge(
    "backup_last_status",
    "1 if the most recent backup attempt succeeded, else 0.",
    registry=REGISTRY,
)

BACKUP_LAST_SIZE_BYTES = Gauge(
    "backup_last_size_bytes",
    "Size of the most recent successful backup file.",
    registry=REGISTRY,
)

BACKUP_DISK_FREE_RATIO = Gauge(
    "backup_disk_free_ratio",
    "Free space ratio (0-1) on the backup volume, as reported by the most recent attempt.",
    registry=REGISTRY,
)


def refresh_gauges() -> None:
    """Recomputed on every /metrics scrape rather than kept live in memory —
    scrape intervals (typically 15-60s) make a fresh query cheaper and
    simpler than keeping counters in sync with the database from every
    request/task path. Each block is isolated so one failing probe (e.g. a
    down dependency) doesn't blank out the others."""
    from django.utils import timezone

    from apps.webhooks.models import WebhookEvent
    from apps.messaging.models import Message
    from core.health.checks import check_cache, check_celery_workers, check_database

    try:
        for check in (check_database(), check_cache()):
            DEPENDENCY_UP.labels(dependency=check.name).set(1 if check.ok else 0)
    except Exception:
        pass

    try:
        workers = check_celery_workers()
        DEPENDENCY_UP.labels(dependency="celery_workers").set(1 if workers.ok else 0)
        CELERY_WORKERS_ONLINE.set(int(workers.detail.split()[0]) if workers.ok else 0)
    except Exception:
        pass

    try:
        WEBHOOK_EVENTS_BY_STATUS._metrics.clear()  # type: ignore[attr-defined]
        for row in WebhookEvent.objects.values("status").annotate(count=Count("id")):
            WEBHOOK_EVENTS_BY_STATUS.labels(status=row["status"]).set(row["count"])
    except Exception:
        pass

    try:
        MESSAGES_FAILED_BY_ERROR_CODE._metrics.clear()  # type: ignore[attr-defined]
        since = timezone.now() - timezone.timedelta(hours=24)
        rows = (
            Message.objects.filter(status="failed", occurred_at__gte=since)
            .values("error_code")
            .annotate(count=Count("id"))
        )
        for row in rows:
            MESSAGES_FAILED_BY_ERROR_CODE.labels(error_code=row["error_code"] or "unknown").set(row["count"])
    except Exception:
        pass

    try:
        import redis

        from django.conf import settings
        from workers.queues import QUEUE_NAMES

        client = redis.Redis.from_url(settings.CELERY_BROKER_URL)
        for queue in QUEUE_NAMES:
            CELERY_QUEUE_DEPTH.labels(queue=queue).set(client.llen(queue))
    except Exception:
        pass

    try:
        refresh_backup_gauges()
    except Exception:
        # Still must not break the scrape, but a silent failure here would
        # leave stale backup gauges (and hide a missing backup) unnoticed.
        logging.getLogger(__name__).exception("could not refresh backup gauges")


def refresh_backup_gauges() -> None:
    """Backup gauges from core.BackupRecord (written by the backup service).

    A prometheus_client Gauge exports 0 until set, which would make "no
    successful backup, ever" look like "a backup just succeeded" (age 0). So
    the cases with nothing to report are explicit: age +Inf (trips
    BackupMissing) and status NaN (so BackupFailed's ``== 0`` stays quiet when
    there is simply no attempt on record yet, rather than reporting a false
    failure)."""
    from django.utils import timezone

    from core.models import BackupRecord

    last = BackupRecord.objects.order_by("-started_at").first()
    # NaN (not the Gauge default 0) when unknown, so "< 0.15" and "== 0"
    # alert expressions don't fire on missing data.
    BACKUP_LAST_STATUS.set(float("nan"))
    BACKUP_DISK_FREE_RATIO.set(float("nan"))
    if last is not None:
        BACKUP_LAST_STATUS.set(1 if last.status == BackupRecord.Status.SUCCEEDED else 0)
        if last.disk_free_bytes is not None and last.disk_total_bytes:
            BACKUP_DISK_FREE_RATIO.set(last.disk_free_bytes / last.disk_total_bytes)

    last_ok = (
        BackupRecord.objects.filter(status=BackupRecord.Status.SUCCEEDED)
        .order_by("-started_at")
        .first()
    )
    if last_ok is None:
        BACKUP_LAST_SUCCESS_AGE_SECONDS.set(float("inf"))
    else:
        BACKUP_LAST_SUCCESS_AGE_SECONDS.set((timezone.now() - last_ok.started_at).total_seconds())
        if last_ok.file_size_bytes:
            BACKUP_LAST_SIZE_BYTES.set(last_ok.file_size_bytes)
