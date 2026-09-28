"""Publish domain events to connected browsers.

Events are published only *after the database transaction commits*, so a
client never sees an event for state that was rolled back, and a client
refetching on the event always finds the committed row.

Routing: one channel group per business (``biz.<business_id>``), plus one
per membership (``member.<membership_id>``) for notifications, which are
personal rather than business-scoped. A socket joins only businesses its
membership may access, so business isolation holds for realtime exactly as
for REST; the membership group is joined only for that same authorized
membership. Payloads carry identifiers and small summaries; clients fetch
full resources through the REST API.
"""

import logging
from typing import Any
from uuid import UUID

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

EVENT_MESSAGE_CREATED = "message.created"
EVENT_MESSAGE_STATUS_CHANGED = "message.status_changed"
EVENT_CONVERSATION_UPDATED = "conversation.updated"
EVENT_NOTIFICATION_CREATED = "notification.created"


def business_group(business_id: UUID | str) -> str:
    return f"biz.{business_id}"


def membership_group(membership_id: UUID | str) -> str:
    return f"member.{membership_id}"


def _send_to_group(group: str, event_type: str, data: dict[str, Any]) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(
            group,
            {
                "type": "domain.event",
                "event": event_type,
                "data": data,
                "emitted_at": timezone.now().isoformat(),
            },
        )
    except Exception:
        # Realtime is best-effort: the database is the source of truth and
        # clients resync over REST on reconnect. Never fail the caller's
        # operation because a socket fan-out failed (e.g. Redis down).
        logger.exception("realtime publish failed", extra={"event": event_type})


def _send(business_id: UUID | str, event_type: str, data: dict[str, Any]) -> None:
    """Business-group send, kept as a thin wrapper around _send_to_group:
    tests/integration/test_realtime.py imports this name directly to inject
    events without waiting on transaction.on_commit."""
    _send_to_group(business_group(business_id), event_type, data)


def publish_on_commit(business_id: UUID | str, event_type: str, data: dict[str, Any]) -> None:
    transaction.on_commit(lambda: _send(business_id, event_type, data))


def publish_notification_on_commit(membership_id: UUID | str, data: dict[str, Any]) -> None:
    transaction.on_commit(
        lambda: _send_to_group(membership_group(membership_id), EVENT_NOTIFICATION_CREATED, data)
    )
