"""Contact resolution (public interface of the contacts module).

Contacts are scoped to a Business: the same WhatsApp user messaging two
businesses of one organization becomes two contacts, so neither business can
see the other's customers. Identity uniqueness is (business, kind, scope, value).
"""

import re
from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.businesses.models import Business
from apps.contacts.models import Contact, ContactIdentity

_PHONE = re.compile(r"^[1-9]\d{6,14}$")
_BSUID = re.compile(r"^[A-Z]{2}\.(ENT\.)?[A-Za-z0-9]{1,128}$")


class InvalidIdentifierError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Digits-only international format expected by WhatsApp (no '+', no
    leading zeros). Raises for values that cannot be a valid number."""
    digits = re.sub(r"[\s\-().]", "", raw or "").removeprefix("+")
    if not _PHONE.match(digits):
        raise InvalidIdentifierError("phone number must be 7-15 digits with country code")
    return digits


def validate_bsuid(raw: str) -> str:
    if not _BSUID.match(raw or ""):
        raise InvalidIdentifierError("invalid business-scoped user id")
    return raw


@dataclass(frozen=True)
class ContactIdentifiers:
    phone_number: str | None = None
    bsuid: str | None = None
    parent_bsuid: str | None = None
    bsuid_scope: str = ""
    profile_name: str = ""


def _lookup(business: Business, ids: ContactIdentifiers) -> Contact | None:
    candidates: list[tuple[str, str, str]] = []
    if ids.phone_number:
        candidates.append((ContactIdentity.Kind.PHONE, "", ids.phone_number))
    if ids.bsuid:
        candidates.append((ContactIdentity.Kind.BSUID, ids.bsuid_scope, ids.bsuid))
    for kind, scope, value in candidates:
        identity = (
            ContactIdentity.objects.filter(business=business, kind=kind, scope=scope, value=value)
            .select_related("contact")
            .first()
        )
        if identity:
            return identity.contact
    return None


def _attach(contact: Contact, kind: str, scope: str, value: str) -> None:
    try:
        with transaction.atomic():
            ContactIdentity.objects.get_or_create(
                business_id=contact.business_id,
                kind=kind,
                scope=scope,
                value=value,
                defaults={"contact": contact, "organization_id": contact.organization_id},
            )
    except IntegrityError:
        # Concurrent resolver attached it first; uniqueness holds either way.
        pass


@transaction.atomic
def resolve_contact(business: Business, ids: ContactIdentifiers) -> Contact:
    """Find or create the business's contact for these identifiers and link
    any newly learned identifier (e.g. a BSUID seen for a known phone number).
    Idempotent and safe under concurrent webhook processing."""
    if not (ids.phone_number or ids.bsuid):
        raise InvalidIdentifierError("a phone number or BSUID is required")

    contact = _lookup(business, ids)
    if contact is None:
        try:
            with transaction.atomic():
                contact = Contact.objects.create(
                    organization_id=business.organization_id,
                    business=business,
                    phone_number=ids.phone_number or "",
                    profile_name=ids.profile_name,
                    country_code=(ids.bsuid or "")[:2] if ids.bsuid else "",
                )
                first_kind, first_scope, first_value = (
                    (ContactIdentity.Kind.PHONE, "", ids.phone_number)
                    if ids.phone_number
                    else (ContactIdentity.Kind.BSUID, ids.bsuid_scope, ids.bsuid or "")
                )
                ContactIdentity.objects.create(
                    organization_id=business.organization_id,
                    business=business,
                    contact=contact,
                    kind=first_kind,
                    scope=first_scope,
                    value=first_value,
                )
        except IntegrityError:
            contact = _lookup(business, ids)
            if contact is None:
                raise

    if ids.phone_number:
        _attach(contact, ContactIdentity.Kind.PHONE, "", ids.phone_number)
        if not contact.phone_number:
            contact.phone_number = ids.phone_number
            contact.save(update_fields=["phone_number", "updated_at"])
    if ids.bsuid:
        _attach(contact, ContactIdentity.Kind.BSUID, ids.bsuid_scope, ids.bsuid)
    if ids.parent_bsuid:
        _attach(contact, ContactIdentity.Kind.PARENT_BSUID, ids.bsuid_scope, ids.parent_bsuid)
    if ids.profile_name and ids.profile_name != contact.profile_name:
        contact.profile_name = ids.profile_name
        contact.save(update_fields=["profile_name", "updated_at"])
    return contact


def record_contact_activity(contact: Contact, *, at: datetime, inbound: bool) -> None:
    """Forward-only activity timestamps (late webhooks never rewind them)."""
    qs = Contact.objects.filter(pk=contact.pk)
    qs.filter(Q(last_message_at__isnull=True) | Q(last_message_at__lt=at)).update(
        last_message_at=at
    )
    if inbound:
        qs.filter(Q(last_inbound_at__isnull=True) | Q(last_inbound_at__lt=at)).update(
            last_inbound_at=at
        )
