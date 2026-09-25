"""Asynchronous webhook processing, retries and replay."""

import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.webhooks.handlers import HandledItem, ItemOutcome, handle
from apps.webhooks.models import WebhookEvent, WebhookProcessingAttempt
from core.context import bind
from providers.whatsapp.registry import get_webhook_adapter

logger = logging.getLogger(__name__)

# Retry schedule for failed processing (seconds), capped at the last value.
RETRY_SCHEDULE = (15, 60, 300, 900, 1800, 3600)
# Unmatched status reports are retried quickly: the race is usually seconds.
STATUS_RACE_RETRY_SECONDS = 20
PROCESSING_STUCK_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class ProcessResult:
    status: str
    counts: dict[str, int]


def _claim(
    event_id: UUID | str, trigger: str
) -> tuple[WebhookEvent, WebhookProcessingAttempt] | None:
    with transaction.atomic():
        event = (
            WebhookEvent.objects.select_for_update(skip_locked=True)
            .filter(
                pk=event_id, status__in=[WebhookEvent.Status.PENDING, WebhookEvent.Status.FAILED]
            )
            .first()
        )
        if event is None:
            return None
        event.status = WebhookEvent.Status.PROCESSING
        event.attempts += 1
        event.save(update_fields=["status", "attempts", "updated_at"])
        attempt = WebhookProcessingAttempt.objects.create(
            event=event, attempt=event.attempts, trigger=trigger, started_at=timezone.now()
        )
        return event, attempt


def _finish(
    event: WebhookEvent,
    attempt: WebhookProcessingAttempt,
    *,
    status: str,
    outcome: str,
    counts: Counter[str],
    organization_ids: set[UUID],
    error: str = "",
    retry_in: int | None = None,
    started: float,
) -> None:
    now = timezone.now()
    event.status = status
    event.last_error = error[:4000]
    event.organization_ids = sorted({*event.organization_ids, *organization_ids}, key=str)
    event.next_attempt_at = now + timedelta(seconds=retry_in) if retry_in else None
    if status in (WebhookEvent.Status.PROCESSED, WebhookEvent.Status.PARTIAL):
        event.processed_at = now
    event.save(
        update_fields=[
            "status",
            "last_error",
            "organization_ids",
            "next_attempt_at",
            "processed_at",
            "updated_at",
        ]
    )
    attempt.finished_at = now
    attempt.outcome = outcome
    attempt.items_total = sum(counts.values())
    attempt.items_applied = counts[ItemOutcome.APPLIED]
    attempt.items_skipped = counts[ItemOutcome.UNROUTABLE]
    attempt.error = error[:4000]
    attempt.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    attempt.save()


def process_event(event_id: UUID | str, *, trigger: str = "queue") -> ProcessResult:
    claimed = _claim(event_id, trigger)
    if claimed is None:
        return ProcessResult(status="skipped", counts={})
    event, attempt = claimed
    started = time.perf_counter()
    counts: Counter[str] = Counter()
    organization_ids: set[UUID] = set()

    with bind(request_id=event.request_id or None):
        try:
            items = get_webhook_adapter(event.provider).parse(event.payload)
            for item in items:
                handled: HandledItem = handle(item, event=event)
                counts[handled.outcome] += 1
                if handled.organization_id:
                    organization_ids.add(handled.organization_id)
        except Exception as exc:
            logger.exception("webhook processing failed", extra={"event_id": str(event.pk)})
            dead = event.attempts >= settings.WEBHOOK_MAX_PROCESSING_ATTEMPTS
            _finish(
                event,
                attempt,
                status=WebhookEvent.Status.DEAD if dead else WebhookEvent.Status.FAILED,
                outcome=WebhookProcessingAttempt.Outcome.FAILED,
                counts=counts,
                organization_ids=organization_ids,
                error=f"{type(exc).__name__}: {exc}",
                retry_in=(
                    None if dead else RETRY_SCHEDULE[min(event.attempts, len(RETRY_SCHEDULE)) - 1]
                ),
                started=started,
            )
            return ProcessResult(status=event.status, counts=dict(counts))

    if counts[ItemOutcome.RETRY]:
        status, outcome, retry_in = (
            WebhookEvent.Status.FAILED,
            WebhookProcessingAttempt.Outcome.RETRY,
            STATUS_RACE_RETRY_SECONDS,
        )
        error = f"{counts[ItemOutcome.RETRY]} status report(s) awaiting their message"
    elif counts[ItemOutcome.UNROUTABLE]:
        status, outcome, retry_in, error = (
            WebhookEvent.Status.PARTIAL,
            WebhookProcessingAttempt.Outcome.PARTIAL,
            None,
            "",
        )
    else:
        status, outcome, retry_in, error = (
            WebhookEvent.Status.PROCESSED,
            WebhookProcessingAttempt.Outcome.PROCESSED,
            None,
            "",
        )
    _finish(
        event,
        attempt,
        status=status,
        outcome=outcome,
        counts=counts,
        organization_ids=organization_ids,
        error=error,
        retry_in=retry_in,
        started=started,
    )
    logger.info(
        "webhook processed",
        extra={
            "event_id": str(event.pk),
            "status": status,
            **{f"items_{k}": v for k, v in counts.items()},
        },
    )
    return ProcessResult(status=status, counts=dict(counts))


def due_event_ids(limit: int = 500) -> list[Any]:
    """Events whose (re)processing is due. PENDING rows get a grace period so
    the sweeper does not race the enqueue that follows ingestion."""
    now = timezone.now()
    grace = now - timedelta(seconds=30)
    # Recover events whose worker died mid-processing.
    WebhookEvent.objects.filter(
        status=WebhookEvent.Status.PROCESSING, updated_at__lt=now - PROCESSING_STUCK_AFTER
    ).update(status=WebhookEvent.Status.FAILED, next_attempt_at=now, updated_at=now)
    return list(
        WebhookEvent.objects.filter(
            status__in=[WebhookEvent.Status.PENDING, WebhookEvent.Status.FAILED],
            next_attempt_at__lte=now,
        )
        .exclude(status=WebhookEvent.Status.PENDING, received_at__gt=grace)
        .order_by("next_attempt_at")
        .values_list("pk", flat=True)[:limit]
    )


def mark_for_replay(event: WebhookEvent) -> None:
    """Reset an event so the processor runs it again. Safe for PROCESSED
    events too: every domain write is idempotent."""
    WebhookEvent.objects.filter(pk=event.pk).exclude(status=WebhookEvent.Status.PROCESSING).update(
        status=WebhookEvent.Status.PENDING,
        next_attempt_at=timezone.now(),
        attempts=0,
        updated_at=timezone.now(),
    )
