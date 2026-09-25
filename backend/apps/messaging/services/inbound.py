"""Inbound message ingestion (called by the webhook processor)."""

from dataclasses import dataclass
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.contacts.services import ContactIdentifiers, record_contact_activity, resolve_contact
from apps.conversations.services import get_or_create_conversation, record_inbound_activity
from apps.messaging.models import Direction, Message, MessageAttachment, MessageType
from apps.messaging.services import events
from apps.messaging.services.errors import UnroutableEventError
from apps.messaging.services.media_download import enqueue_download
from apps.messaging.state import MessageStatus
from apps.whatsapp.models import WhatsAppPhoneNumber
from providers.whatsapp.types import InboundMessageEvent


@dataclass(frozen=True)
class InboundResult:
    message: Message
    created: bool


def _message_type(raw: str) -> str:
    return raw if raw in MessageType.values else MessageType.UNSUPPORTED


def _find_number(provider: str, external_id: str) -> WhatsAppPhoneNumber:
    number = (
        WhatsAppPhoneNumber.objects.select_related("business_account", "business")
        .filter(provider=provider, external_id=external_id)
        .first()
    )
    if number is None:
        raise UnroutableEventError(f"unknown phone number id {external_id}")
    return number


@transaction.atomic
def ingest_inbound_message(
    event: InboundMessageEvent, *, provider: str, source_event_id: UUID | None = None
) -> InboundResult:
    number = _find_number(provider, event.phone_number_external_id)

    existing = Message.objects.filter(
        phone_number=number, direction=Direction.INBOUND, external_id=event.external_message_id
    ).first()
    if existing:
        return InboundResult(existing, created=False)

    contact = resolve_contact(
        number.business,
        ContactIdentifiers(
            phone_number=event.sender.phone_number,
            bsuid=event.sender.bsuid,
            parent_bsuid=event.sender.parent_bsuid,
            bsuid_scope=number.business_account.bsuid_scope,
            profile_name=event.sender.name,
        ),
    )
    conversation, conversation_created = get_or_create_conversation(number, contact)
    message_type = _message_type(event.message_type)
    content = dict(event.content)
    if message_type == MessageType.UNSUPPORTED and event.message_type != "unsupported":
        content["provider_type"] = event.message_type

    try:
        with transaction.atomic():
            message = Message.objects.create(
                organization_id=number.organization_id,
                business_id=number.business_id,
                conversation=conversation,
                phone_number=number,
                contact=contact,
                direction=Direction.INBOUND,
                message_type=message_type,
                status=MessageStatus.RECEIVED,
                provider=provider,
                external_id=event.external_message_id,
                content=content,
                text=event.text,
                reply_to_external_id=event.reply_to_external_id or "",
                provider_metadata={"message": event.raw} if event.raw else {},
                occurred_at=event.occurred_at,
                status_changed_at=event.occurred_at,
            )
    except IntegrityError:
        # Concurrent duplicate delivery committed first.
        existing = Message.objects.get(
            phone_number=number, direction=Direction.INBOUND, external_id=event.external_message_id
        )
        return InboundResult(existing, created=False)

    if event.media:
        attachment = MessageAttachment.objects.create(
            message=message,
            kind=message_type,
            provider_media_id=str(event.media.get("id") or ""),
            mime_type=str(event.media.get("mime_type") or ""),
            sha256=str(event.media.get("sha256") or ""),
            filename=str(event.media.get("filename") or ""),
            caption=str(event.media.get("caption") or ""),
        )
        attachment_id = attachment.pk
        transaction.on_commit(lambda: enqueue_download(attachment_id))

    record_inbound_activity(
        conversation, at=event.occurred_at, preview=event.text or f"[{message_type}]"
    )
    record_contact_activity(contact, at=event.occurred_at, inbound=True)
    events.message_created(message)
    if conversation_created:
        events.conversation_created(conversation)
    else:
        events.conversation_updated(conversation)
    return InboundResult(message, created=True)
