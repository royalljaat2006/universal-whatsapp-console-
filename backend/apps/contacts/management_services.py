"""Contact profile, notes and tags (business-scoped; public interface).

Every mutation takes the target object already resolved through a
business-scoped queryset by the caller, and re-checks that tags belong to
the same business as the contact/conversation they are applied to.
"""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.audit.services import record as audit
from apps.businesses.models import Business
from apps.contacts.models import Contact, ContactNote, Tag
from apps.conversations.models import Conversation
from apps.messaging.services import events
from core.exceptions import ConflictError, DomainError, NotFoundError

TAG_COLORS = {"", "gray", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"}


class InvalidTagError(DomainError):
    code = "INVALID_TAG"


def _clean_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned or len(cleaned) > 64:
        raise InvalidTagError("Tag names must be 1 to 64 characters.")
    return cleaned


@transaction.atomic
def create_tag(*, business: Business, name: str, color: str = "", actor: Any = None) -> Tag:
    if color not in TAG_COLORS:
        raise InvalidTagError("Unsupported tag color.")
    cleaned = _clean_name(name)
    if (
        Tag.objects.filter(business=business)
        .annotate(n=Lower("name"))
        .filter(n=cleaned.lower())
        .exists()
    ):
        raise ConflictError(f"A tag named '{cleaned}' already exists in this business.")
    try:
        with transaction.atomic():
            tag = Tag.objects.create(
                organization_id=business.organization_id,
                business=business,
                name=cleaned,
                color=color,
            )
    except IntegrityError as exc:
        raise ConflictError(f"A tag named '{cleaned}' already exists in this business.") from exc
    audit(
        "tag.created",
        organization=business.organization,
        actor=actor,
        resource=tag,
        metadata={"name": cleaned},
    )
    return tag


@transaction.atomic
def update_tag(
    tag: Tag, *, name: str | None = None, color: str | None = None, actor: Any = None
) -> Tag:
    if name is not None:
        cleaned = _clean_name(name)
        clash = (
            Tag.objects.filter(business_id=tag.business_id)
            .exclude(pk=tag.pk)
            .annotate(n=Lower("name"))
            .filter(n=cleaned.lower())
            .exists()
        )
        if clash:
            raise ConflictError(f"A tag named '{cleaned}' already exists in this business.")
        tag.name = cleaned
    if color is not None:
        if color not in TAG_COLORS:
            raise InvalidTagError("Unsupported tag color.")
        tag.color = color
    tag.save()
    audit(
        "tag.updated",
        organization=tag.organization,
        actor=actor,
        resource=tag,
        metadata={"name": tag.name},
    )
    return tag


@transaction.atomic
def delete_tag(tag: Tag, *, actor: Any = None) -> None:
    conversations = list(tag.conversations.all())
    audit(
        "tag.deleted",
        organization=tag.organization,
        actor=actor,
        resource=tag,
        metadata={"name": tag.name},
    )
    tag.delete()
    for conversation in conversations:
        events.conversation_tags_changed(conversation)


def _tag_for(business_id: Any, tag_id: Any) -> Tag:
    tag = Tag.objects.filter(pk=tag_id, business_id=business_id).first()
    if tag is None:
        raise NotFoundError("tag not found")
    return tag


@transaction.atomic
def tag_contact(contact: Contact, tag_id: Any, *, add: bool) -> Contact:
    tag = _tag_for(contact.business_id, tag_id)
    (contact.tags.add if add else contact.tags.remove)(tag)
    events.contact_updated(contact)
    return contact


@transaction.atomic
def tag_conversation(conversation: Conversation, tag_id: Any, *, add: bool) -> Conversation:
    tag = _tag_for(conversation.business_id, tag_id)
    (conversation.tags.add if add else conversation.tags.remove)(tag)
    events.conversation_tags_changed(conversation)
    return conversation


EDITABLE_CONTACT_FIELDS = {"display_name", "attributes", "opt_in_status", "opt_in_source"}


@transaction.atomic
def update_contact(contact: Contact, changes: dict[str, Any], *, actor: Any = None) -> Contact:
    unknown = set(changes) - EDITABLE_CONTACT_FIELDS
    if unknown:
        raise DomainError(f"Fields cannot be edited: {', '.join(sorted(unknown))}")
    for field, value in changes.items():
        setattr(contact, field, value)
    contact.save()
    audit(
        "contact.updated",
        organization=contact.organization,
        actor=actor,
        resource=contact,
        metadata={"fields": sorted(changes)},
    )
    events.contact_updated(contact)
    return contact


@transaction.atomic
def add_note(contact: Contact, body: str, *, author: Any) -> ContactNote:
    text = (body or "").strip()
    if not text:
        raise DomainError("A note cannot be empty.")
    note = ContactNote.objects.create(
        organization_id=contact.organization_id,
        business_id=contact.business_id,
        contact=contact,
        author=author if getattr(author, "is_authenticated", False) else None,
        body=text[:5000],
    )
    events.contact_updated(contact)
    return note


@transaction.atomic
def delete_note(note: ContactNote, *, actor: Any) -> None:
    contact = note.contact
    audit("contact.note_deleted", organization=contact.organization, actor=actor, resource=note)
    note.delete()
    events.contact_updated(contact)


@transaction.atomic
def erase_contact(contact: Contact, *, actor: Any) -> Contact:
    """GDPR-style erasure. The row survives — Message.contact is PROTECT, so
    billing/analytics records that reference this contact stay intact — but
    every personal-data field is cleared, its notes deleted, its identities
    (phone/BSUID) removed, and its message text/content blanked. Irreversible;
    refuses a contact already erased."""
    if contact.erased_at is not None:
        raise ConflictError("This contact's personal data has already been erased.")

    from apps.messaging.models import Message

    redacted = Message.objects.filter(contact=contact).update(text="", content={})
    contact.note_entries.all().delete()
    contact.identities.all().delete()
    contact.tags.clear()

    contact.display_name = ""
    contact.profile_name = ""
    contact.phone_number = ""
    contact.country_code = ""
    contact.attributes = {}
    contact.notes = ""
    contact.metadata = {}
    contact.erased_at = timezone.now()
    contact.save()

    audit(
        "contact.erased",
        organization=contact.organization,
        actor=actor,
        resource=contact,
        metadata={"messages_redacted": redacted},
    )
    events.contact_updated(contact)
    return contact
