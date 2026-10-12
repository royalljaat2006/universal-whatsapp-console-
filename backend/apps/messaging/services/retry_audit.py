"""Audit of what happened to a retry, written where the outcome is learned.

Kept free of outbound/recovery imports so the dispatch and webhook paths can
call it without import cycles. Only ids, a category and a provider code are
recorded: no recipient numbers, message text or credentials.
"""

from apps.audit import actions
from apps.audit.services import record as audit
from apps.messaging.models import Message
from apps.messaging.services import failure_categories as fc
from apps.messaging.state import MessageStatus

RETRY_KEY_PREFIX = "retry:"

_OUTCOMES = {
    MessageStatus.DELIVERED: "delivered",
    MessageStatus.READ: "delivered",
    MessageStatus.FAILED: "failed",
}


def original_id_of(message: Message) -> str | None:
    key = message.idempotency_key or ""
    return key[len(RETRY_KEY_PREFIX) :] if key.startswith(RETRY_KEY_PREFIX) else None


def record_outcome(message: Message) -> None:
    """Write one audit entry when a retried message reaches delivered/failed.

    ``delivered`` is written only on the first delivery evidence (``read``
    arrives after it and adds nothing)."""
    original = original_id_of(message)
    outcome = _OUTCOMES.get(message.status)
    if original is None or outcome is None:
        return
    if message.status == MessageStatus.READ and message.delivered_at is not None:
        return
    metadata = {
        "original_message_id": original,
        "message_id": str(message.pk),
        "outcome": outcome,
    }
    if outcome == "failed":
        details = message.error_details or {}
        metadata["category"] = fc.categorize(message.error_code, details)
        metadata["error_code"] = (message.error_code or "")[:64]
    audit(
        actions.MESSAGE_RETRY_OUTCOME,
        organization=message.organization,
        resource=message,
        metadata=metadata,
    )
