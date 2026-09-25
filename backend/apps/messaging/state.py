"""Message lifecycle state machine (pure functions, no I/O).

Internal, provider-independent states::

    QUEUED -> SENDING -> ACCEPTED -> SENT -> DELIVERED -> READ
                 \\            \\         \\
                  +------------+---------+--> FAILED

- QUEUED / SENDING are owned by our outbound pipeline.
- ACCEPTED = provider returned a message id synchronously (Meta's
  ``message_status: accepted``); it is *not* yet "sent" to WhatsApp.
- SENT / DELIVERED / READ / FAILED arrive asynchronously via webhooks, in
  any order (Meta does not guarantee chronological delivery).

Rules (docs/messaging-architecture.md#state-transitions):

1. Progress states only move forward by rank; a late ``delivered`` never
   overwrites ``read``.
2. FAILED is accepted only before delivery evidence exists
   (QUEUED/SENDING/ACCEPTED/SENT).
3. Delivery evidence (DELIVERED/READ) supersedes FAILED — the recipient
   demonstrably got the message.
4. Every provider event is still *recorded* (MessageStatusEvent) and its
   per-status timestamp filled, even when it does not change the status.
"""

from django.db import models


class MessageStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    ACCEPTED = "accepted", "Accepted by provider"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    READ = "read", "Read"
    FAILED = "failed", "Failed"
    # Inbound messages are simply RECEIVED; they do not follow the outbound path.
    RECEIVED = "received", "Received"


PROGRESS_RANK: dict[str, int] = {
    MessageStatus.QUEUED: 0,
    MessageStatus.SENDING: 1,
    MessageStatus.ACCEPTED: 2,
    MessageStatus.SENT: 3,
    MessageStatus.DELIVERED: 4,
    MessageStatus.READ: 5,
}

_FAILABLE = {
    MessageStatus.QUEUED,
    MessageStatus.SENDING,
    MessageStatus.ACCEPTED,
    MessageStatus.SENT,
}
_DELIVERY_EVIDENCE = {MessageStatus.DELIVERED, MessageStatus.READ}

TIMESTAMP_FIELD: dict[str, str] = {
    MessageStatus.ACCEPTED: "accepted_at",
    MessageStatus.SENT: "sent_at",
    MessageStatus.DELIVERED: "delivered_at",
    MessageStatus.READ: "read_at",
    MessageStatus.FAILED: "failed_at",
}

# Meta webhook ``statuses[].status`` values (verified: sent, delivered, read,
# failed, played). ``played`` (voice message played) is read-equivalent; the
# raw value is preserved on MessageStatusEvent.provider_status.
META_STATUS_MAP: dict[str, MessageStatus] = {
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.DELIVERED,
    "read": MessageStatus.READ,
    "played": MessageStatus.READ,
    "failed": MessageStatus.FAILED,
}


def should_transition(current: str, target: str) -> bool:
    """Whether a message in ``current`` should move to ``target``."""
    if current == target or current == MessageStatus.RECEIVED:
        return False
    if target == MessageStatus.FAILED:
        return current in _FAILABLE
    if current == MessageStatus.FAILED:
        return target in _DELIVERY_EVIDENCE
    if target not in PROGRESS_RANK or current not in PROGRESS_RANK:
        return False
    return PROGRESS_RANK[target] > PROGRESS_RANK[current]
