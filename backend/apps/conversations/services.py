"""Conversation services (public interface of the conversations module)."""

from datetime import datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.contacts.models import Contact
from apps.conversations.models import Conversation
from apps.organizations.tenancy import ensure_same_organization
from apps.whatsapp.models import WhatsAppPhoneNumber
from core.exceptions import DomainError


class InvalidConversationScopeError(ValueError):
    """Phone number and contact belong to different businesses."""


class InvalidStatusTransitionError(DomainError):
    code = "INVALID_STATUS"
    default_message = "Unsupported conversation status."


def get_or_create_conversation(
    phone_number: WhatsAppPhoneNumber, contact: Contact
) -> tuple[Conversation, bool]:
    ensure_same_organization(phone_number, contact)
    if phone_number.business_id != contact.business_id:
        raise InvalidConversationScopeError(
            "phone number and contact belong to different businesses"
        )
    lookup = {
        "organization_id": phone_number.organization_id,
        "phone_number": phone_number,
        "contact": contact,
    }
    existing = Conversation.objects.filter(**lookup).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            return (
                Conversation.objects.create(
                    **lookup, business_id=phone_number.business_id, status_changed_at=timezone.now()
                ),
                True,
            )
    except IntegrityError:
        return Conversation.objects.get(**lookup), False


def record_inbound_activity(conversation: Conversation, *, at: datetime, preview: str) -> None:
    """Atomic counters (no read-modify-write races between workers). A new
    inbound message re-opens resolved/archived conversations. Timestamps only
    move forward, so a late-delivered older message cannot rewind them."""
    Conversation.objects.filter(pk=conversation.pk).update(unread_count=F("unread_count") + 1)
    Conversation.objects.filter(pk=conversation.pk).exclude(status=Conversation.Status.OPEN).update(
        status=Conversation.Status.OPEN, status_changed_at=timezone.now()
    )
    _advance(conversation, at=at, preview=preview, direction=Conversation.Direction.INBOUND)


def record_outbound_activity(conversation: Conversation, *, at: datetime, preview: str) -> None:
    """An agent reply marks the thread as read (they are looking at it)."""
    Conversation.objects.filter(pk=conversation.pk).update(unread_count=0)
    _advance(conversation, at=at, preview=preview, direction=Conversation.Direction.OUTBOUND)


def _advance(conversation: Conversation, *, at: datetime, preview: str, direction: str) -> None:
    qs = Conversation.objects.filter(pk=conversation.pk)
    qs.filter(Q(last_message_at__isnull=True) | Q(last_message_at__lt=at)).update(
        last_message_at=at, last_message_preview=preview[:200], last_message_direction=direction
    )
    if direction == Conversation.Direction.INBOUND:
        qs.filter(Q(last_inbound_at__isnull=True) | Q(last_inbound_at__lt=at)).update(
            last_inbound_at=at
        )
    conversation.refresh_from_db()


def mark_read(conversation: Conversation) -> Conversation:
    Conversation.objects.filter(pk=conversation.pk).update(
        unread_count=0, updated_at=timezone.now()
    )
    conversation.refresh_from_db()
    return conversation


def change_status(
    conversation: Conversation, status: str, *, actor: Any = None, request: Any = None
) -> Conversation:
    if status not in Conversation.Status.values:
        raise InvalidStatusTransitionError()
    if conversation.status == status:
        return conversation
    previous = conversation.status
    conversation.status = status
    conversation.status_changed_at = timezone.now()
    conversation.save(update_fields=["status", "status_changed_at", "updated_at"])
    audit(
        actions.CONVERSATION_STATUS_CHANGED,
        organization=conversation.organization,
        actor=actor,
        resource=conversation,
        metadata={"from": previous, "to": status},
        request=request,
    )
    return conversation
