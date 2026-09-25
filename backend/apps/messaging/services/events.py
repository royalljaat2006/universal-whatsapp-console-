"""Realtime event payloads (ids + small summaries only; published after commit
to the owning business's channel). Clients re-read resources over REST."""

import logging
from typing import Any

from apps.conversations.models import Conversation
from apps.messaging.models import Message, MessageAttachment
from apps.messaging.services import hooks
from apps.realtime import publisher

lifecycle_logger = logging.getLogger("messaging.lifecycle")

MESSAGE_CREATED = publisher.EVENT_MESSAGE_CREATED
MESSAGE_STATUS_CHANGED = publisher.EVENT_MESSAGE_STATUS_CHANGED
MESSAGE_MEDIA_READY = "message.media_ready"
CONVERSATION_CREATED = "conversation.created"
CONVERSATION_UPDATED = publisher.EVENT_CONVERSATION_UPDATED
CONVERSATION_ASSIGNED = "conversation.assigned"
CONVERSATION_TAGS_CHANGED = "conversation.tags_changed"
CONTACT_UPDATED = "contact.updated"
NUMBER_STATUS_CHANGED = "number.status_changed"
ACCOUNT_ALERT = "account.alert"


def _message_summary(message: Message) -> dict[str, Any]:
    return {
        "id": str(message.pk),
        "business_id": str(message.business_id),
        "conversation_id": str(message.conversation_id),
        "phone_number_id": str(message.phone_number_id),
        "direction": message.direction,
        "message_type": message.message_type,
        "status": message.status,
        "error_code": message.error_code,
        "occurred_at": message.occurred_at.isoformat(),
    }


def _log(event: str, message: Message) -> None:
    lifecycle_logger.info(
        event,
        extra={
            "message_id": str(message.pk),
            "business_id": str(message.business_id),
            "direction": message.direction,
            "status": message.status,
            "external_id": message.external_id,
            "error_code": message.error_code or None,
        },
    )


def message_created(message: Message) -> None:
    _log("message created", message)
    hooks.message_changed(message)
    publisher.publish_on_commit(message.business_id, MESSAGE_CREATED, _message_summary(message))


def message_status_changed(message: Message) -> None:
    _log("message status changed", message)
    hooks.message_changed(message)
    publisher.publish_on_commit(
        message.business_id, MESSAGE_STATUS_CHANGED, _message_summary(message)
    )


def media_updated(attachment: MessageAttachment) -> None:
    message = attachment.message
    publisher.publish_on_commit(
        message.business_id,
        MESSAGE_MEDIA_READY,
        {
            **_message_summary(message),
            "attachment_id": str(attachment.pk),
            "storage_status": attachment.storage_status,
        },
    )


def _conversation_summary(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": str(conversation.pk),
        "business_id": str(conversation.business_id),
        "phone_number_id": str(conversation.phone_number_id),
        "status": conversation.status,
        "unread_count": conversation.unread_count,
        "assigned_membership_id": (
            str(conversation.assigned_membership_id)
            if conversation.assigned_membership_id
            else None
        ),
        "assigned_team_id": (
            str(conversation.assigned_team_id) if conversation.assigned_team_id else None
        ),
        "last_message_preview": conversation.last_message_preview,
        "last_message_at": (
            conversation.last_message_at.isoformat() if conversation.last_message_at else None
        ),
    }


def conversation_created(conversation: Conversation) -> None:
    publisher.publish_on_commit(
        conversation.business_id, CONVERSATION_CREATED, _conversation_summary(conversation)
    )


def conversation_updated(conversation: Conversation) -> None:
    publisher.publish_on_commit(
        conversation.business_id, CONVERSATION_UPDATED, _conversation_summary(conversation)
    )


def conversation_assigned(conversation: Conversation) -> None:
    publisher.publish_on_commit(
        conversation.business_id, CONVERSATION_ASSIGNED, _conversation_summary(conversation)
    )


def conversation_tags_changed(conversation: Conversation) -> None:
    publisher.publish_on_commit(
        conversation.business_id, CONVERSATION_TAGS_CHANGED, _conversation_summary(conversation)
    )


def contact_updated(contact: Any) -> None:
    publisher.publish_on_commit(
        contact.business_id,
        CONTACT_UPDATED,
        {"id": str(contact.pk), "business_id": str(contact.business_id)},
    )


def number_status_changed(number: Any) -> None:
    publisher.publish_on_commit(
        number.business_id,
        NUMBER_STATUS_CHANGED,
        {
            "id": str(number.pk),
            "business_id": str(number.business_id),
            "status": number.status,
            "quality_rating": number.quality_rating,
            "messaging_limit_tier": number.messaging_limit_tier,
        },
    )


def account_alert(event: Any) -> None:
    publisher.publish_on_commit(
        event.business_id,
        ACCOUNT_ALERT,
        {
            "id": str(event.pk),
            "business_id": str(event.business_id),
            "field": event.field,
            "event": event.event,
            "severity": event.severity,
            "summary": event.summary,
        },
    )
