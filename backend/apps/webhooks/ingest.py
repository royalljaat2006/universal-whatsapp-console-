"""Fast path of webhook ingestion: persist, then enqueue after commit.

Durability argument: the HTTP 200 is returned only after the raw event row
has committed. If the broker is down at that moment the row stays PENDING
and the periodic sweeper enqueues it — Redis loss can delay but never drop
an acknowledged event (transactional-outbox pattern).
"""

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.webhooks.models import WebhookEvent
from core.context import request_id_var
from providers.whatsapp.base import WebhookAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    event: WebhookEvent
    duplicate: bool


def enqueue_processing(event_id: Any) -> None:
    from apps.webhooks.tasks import process_webhook_event

    try:
        process_webhook_event.delay(str(event_id))
    except Exception:
        # Broker unavailable: the sweeper will pick the PENDING row up.
        logger.exception("could not enqueue webhook processing", extra={"event_id": str(event_id)})


def persist_event(
    *,
    provider: str,
    adapter: WebhookAdapter,
    raw_body: bytes,
    payload: Mapping[str, Any],
) -> IngestResult:
    digest = hashlib.sha256(raw_body).hexdigest()
    keys = adapter.routing_keys(payload)
    now = timezone.now()
    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(
                provider=provider,
                object_type=str(payload.get("object") or "")[:64],
                body_sha256=digest,
                payload=dict(payload),
                business_account_external_ids=keys.get("business_accounts", []),
                phone_number_external_ids=keys.get("phone_numbers", []),
                fields=keys.get("fields", []),
                received_at=now,
                next_attempt_at=now,
                request_id=request_id_var.get() or "",
            )
            transaction.on_commit(lambda: enqueue_processing(event.pk))
    except IntegrityError:
        existing = WebhookEvent.objects.get(provider=provider, body_sha256=digest)
        logger.info("duplicate webhook delivery", extra={"event_id": str(existing.pk)})
        return IngestResult(existing, duplicate=True)
    return IngestResult(event, duplicate=False)
