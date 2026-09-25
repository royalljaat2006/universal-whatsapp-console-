"""Routes normalised provider events to the owning domain service.

The ingestion layer orchestrates; domain modules own the state changes.
Each handler returns an ItemOutcome and the tenant it touched.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from apps.messaging.services.errors import StatusTargetNotFoundError, UnroutableEventError
from apps.messaging.services.inbound import ingest_inbound_message
from apps.messaging.services.status import apply_status_update
from apps.templates.services import apply_template_status_event
from apps.webhooks.models import WebhookEvent
from apps.whatsapp.models import WhatsAppBusinessAccount
from providers.whatsapp.types import (
    AccountEvent,
    InboundMessageEvent,
    ProviderEvent,
    StatusUpdateEvent,
    TemplateStatusEvent,
)

logger = logging.getLogger(__name__)


class ItemOutcome(StrEnum):
    APPLIED = "applied"  # state changed
    IGNORED = "ignored"  # legitimate no-op (duplicate, stale, no handler yet)
    UNROUTABLE = "unroutable"  # references an account/number we do not manage
    RETRY = "retry"  # transient ordering race; reprocess later


@dataclass(frozen=True)
class HandledItem:
    outcome: ItemOutcome
    organization_id: UUID | None = None


def _status_retry_window_open(event: WebhookEvent) -> bool:
    window = timedelta(seconds=settings.WEBHOOK_UNMATCHED_STATUS_RETRY_SECONDS)
    return timezone.now() - event.received_at < window


def handle(item: ProviderEvent, *, event: WebhookEvent) -> HandledItem:
    provider = event.provider
    try:
        if isinstance(item, InboundMessageEvent):
            inbound = ingest_inbound_message(item, provider=provider, source_event_id=event.pk)
            return HandledItem(
                ItemOutcome.APPLIED if inbound.created else ItemOutcome.IGNORED,
                inbound.message.organization_id,
            )
        if isinstance(item, StatusUpdateEvent):
            status = apply_status_update(item, provider=provider, source_event_id=event.pk)
            return HandledItem(
                ItemOutcome.APPLIED if status.applied else ItemOutcome.IGNORED,
                status.message.organization_id,
            )
        if isinstance(item, TemplateStatusEvent):
            return _handle_template_status(item, provider)
        if isinstance(item, AccountEvent):
            return _handle_account_event(item, provider, event)
    except UnroutableEventError as exc:
        logger.info("unroutable webhook item", extra={"reason": str(exc)})
        return HandledItem(ItemOutcome.UNROUTABLE)
    except StatusTargetNotFoundError as exc:
        if _status_retry_window_open(event):
            return HandledItem(ItemOutcome.RETRY)
        # Most likely a message sent outside this platform on the same number.
        logger.info("status for unknown message", extra={"external_message_id": str(exc)})
        return HandledItem(ItemOutcome.UNROUTABLE)
    logger.warning("no handler for provider event", extra={"type": type(item).__name__})
    return HandledItem(ItemOutcome.IGNORED)


def _account(provider: str, external_id: str) -> WhatsAppBusinessAccount | None:
    return WhatsAppBusinessAccount.objects.filter(
        provider=provider, external_id=external_id
    ).first()


def _handle_template_status(item: TemplateStatusEvent, provider: str) -> HandledItem:
    account = _account(provider, item.business_account_external_id)
    if account is None:
        return HandledItem(ItemOutcome.UNROUTABLE)
    template = apply_template_status_event(account, item)
    return HandledItem(
        ItemOutcome.APPLIED if template else ItemOutcome.IGNORED, account.organization_id
    )


def _handle_account_event(item: AccountEvent, provider: str, event: WebhookEvent) -> HandledItem:
    """Quality / limit / alert / account notifications (apps/whatsapp/account_events.py)."""
    from apps.whatsapp.account_events import handle_account_event

    account = _account(provider, item.business_account_external_id)
    if account is None:
        return HandledItem(ItemOutcome.UNROUTABLE)
    record = handle_account_event(
        account,
        field=item.field_name,
        value=item.value,
        occurred_at=item.occurred_at,
        source_event_id=event.pk,
    )
    return HandledItem(
        ItemOutcome.APPLIED if record else ItemOutcome.IGNORED, account.organization_id
    )
