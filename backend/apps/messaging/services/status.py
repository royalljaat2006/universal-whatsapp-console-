"""Apply provider status reports to outbound messages."""

from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.messaging.models import Direction, Message, MessageStatusEvent
from apps.messaging.services import events, hooks
from apps.messaging.services.error_catalog import hint
from apps.messaging.services.errors import StatusTargetNotFoundError, UnroutableEventError
from apps.messaging.services.retry_audit import record_outcome as record_retry_outcome
from apps.messaging.state import (
    META_STATUS_MAP,
    TIMESTAMP_FIELD,
    MessageStatus,
    should_transition,
)
from apps.whatsapp.models import WhatsAppPhoneNumber
from providers.whatsapp.types import StatusUpdateEvent


@dataclass(frozen=True)
class StatusResult:
    message: Message
    recorded: bool  # False when this exact report was already processed
    applied: bool  # True when the message status changed


def apply_transition(
    message: Message, target: str, occurred_at: datetime
) -> tuple[bool, list[str]]:
    """Mutate ``message`` in memory; return (status_changed, changed_fields).

    Per-status timestamps are filled even when the status itself does not
    move (e.g. a late ``delivered`` after ``read`` still records delivered_at).
    """
    changed: list[str] = []
    ts_field = TIMESTAMP_FIELD.get(target)
    if ts_field and getattr(message, ts_field) is None:
        setattr(message, ts_field, occurred_at)
        changed.append(ts_field)
    moved = should_transition(message.status, target)
    if moved:
        message.status = target
        message.status_changed_at = occurred_at
        changed += ["status", "status_changed_at"]
    return moved, changed


def _map_status(provider: str, raw: str) -> str:
    if provider == "meta":
        return META_STATUS_MAP.get(raw.lower(), "")
    return ""


@transaction.atomic
def apply_status_update(
    event: StatusUpdateEvent, *, provider: str, source_event_id: UUID | None = None
) -> StatusResult:
    number = WhatsAppPhoneNumber.objects.filter(
        provider=provider, external_id=event.phone_number_external_id
    ).first()
    if number is None:
        raise UnroutableEventError(f"unknown phone number id {event.phone_number_external_id}")

    message = (
        Message.objects.select_for_update()
        .filter(
            phone_number=number,
            direction=Direction.OUTBOUND,
            external_id=event.external_message_id,
        )
        .first()
    )
    if message is None:
        raise StatusTargetNotFoundError(event.external_message_id)

    target = _map_status(provider, event.status)
    errors = [asdict(e) for e in event.errors]
    try:
        with transaction.atomic():
            status_event = MessageStatusEvent.objects.create(
                organization_id=message.organization_id,
                message=message,
                status=target,
                provider_status=event.status,
                occurred_at=event.occurred_at,
                applied=False,
                errors=errors,
                pricing=event.pricing,
                source_event_id=source_event_id,
            )
    except IntegrityError:
        return StatusResult(message, recorded=False, applied=False)

    applied, fields = False, list[str]()
    if target:
        applied, fields = apply_transition(message, target, event.occurred_at)

    if applied and target == MessageStatus.FAILED and errors:
        first = errors[0]
        code = str(first.get("code") or "")
        message.error_code = code[:64]
        message.error_message = hint(
            code, None, str(first.get("message") or first.get("title") or "")
        )[:500]
        message.error_details = {"provider_errors": errors}
        fields += ["error_code", "error_message", "error_details"]

    if event.pricing:
        message.billable = event.pricing.get("billable")
        message.pricing_category = str(event.pricing.get("category") or "")[:40]
        message.pricing_type = str(event.pricing.get("type") or "")[:40]
        fields += ["billable", "pricing_category", "pricing_type"]

    if fields:
        message.save(update_fields=[*set(fields), "updated_at"])
    if applied:
        MessageStatusEvent.objects.filter(pk=status_event.pk).update(applied=True)
        events.message_status_changed(message)
        record_retry_outcome(message)
    elif fields:
        # Timestamps/pricing changed without a status move (e.g. a late
        # "delivered" after "read"): derived analytics still need a refresh.
        hooks.message_changed(message)
    if message.billable is not None and (message.delivered_at or message.billable is False):
        # Cost is computed asynchronously from the provider's own report.
        message_id = message.pk
        transaction.on_commit(lambda: _queue_costing(message_id))
    return StatusResult(message, recorded=True, applied=applied)


def _queue_costing(message_id: UUID) -> None:
    from apps.billing.tasks import calculate_message_cost

    try:
        calculate_message_cost.delay(str(message_id))
    except Exception:  # broker down: the pending sweeper prices it later
        import logging

        logging.getLogger(__name__).exception("could not queue cost calculation")
