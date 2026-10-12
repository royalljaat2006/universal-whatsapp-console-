"""Verified test message: preview -> explicit approval -> real send -> evidence.

Safety model (every rule is enforced here, on the server):

- The content is fixed by the server (an approved template that needs no
  variables). The browser cannot supply free text, so this endpoint cannot be
  used as a general sender.
- The recipient must be authorized: an existing contact of this business who
  opted in, or a number the user explicitly attests has agreed to receive a
  test message (recorded in the audit log). Opted-out contacts are refused.
- A preview sends nothing. It returns a short-lived, single-use *approval
  token* bound to this user, organization, business, sender, recipient and the
  exact content. Sending requires that token plus ``approve: true`` in a
  separate call, and the preview is rebuilt and compared at that moment, so
  anything that changed in between (number disconnected, template no longer
  approved, different recipient) refuses the send.
- The send goes through the normal outbound pipeline (``enqueue_outbound_message``):
  business scope, number-connected check, template rules, queue, rate limits,
  idempotency and audit logging. The idempotency key is derived from the
  approval token, so a double-click or replay cannot send twice.
- Success is never claimed from the request being accepted: the caller reads
  the message's own stored status (accepted / sent / delivered / read / failed)
  as the provider reports it.
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.contacts.models import Contact
from apps.contacts.services import InvalidIdentifierError, normalize_phone
from apps.messaging.models import Message
from apps.messaging.services.outbound import SendRequest, SendScope, TemplateSend
from apps.messaging.services.recovery import estimate_cost
from apps.templates.models import MessageTemplate
from apps.templates.parameters import TemplateParameterError, build, describe
from apps.whatsapp.models import WhatsAppPhoneNumber
from core.exceptions import DomainError, NotFoundError

APPROVAL_TTL_SECONDS = 10 * 60
KEY_PREFIX = "testmsg:"
_CACHE_PREFIX = "wa-testmsg-approval:"


class ApprovalInvalidError(DomainError):
    code = "TEST_APPROVAL_INVALID"
    status_code = 409
    default_message = (
        "This approval is no longer valid. Preview the test message again and approve it again."
    )


class TestMessageBlockedError(DomainError):
    __test__ = False  # not a pytest class
    code = "TEST_MESSAGE_BLOCKED"
    status_code = 409
    default_message = "This test message cannot be sent."


@dataclass
class Preview:
    can_send: bool
    sender: dict[str, str]
    recipient: dict[str, str]
    message_type: str
    content: str
    template: dict[str, str] | None
    cost: dict[str, Any]
    restrictions: list[dict[str, str]] = field(default_factory=list)
    blockers: list[dict[str, str]] = field(default_factory=list)
    authorization: str = ""
    # internal, never serialized
    _number: WhatsAppPhoneNumber | None = None
    _template: MessageTemplate | None = None
    _phone: str = ""
    _content_hash: str = ""


def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def sendable_templates(number: WhatsAppPhoneNumber) -> list[MessageTemplate]:
    """Approved templates of this number's account that need no variables."""
    found: list[MessageTemplate] = []
    for template in MessageTemplate.objects.filter(
        business_account_id=number.business_account_id,
        status="APPROVED",
        missing_from_provider=False,
    ).order_by("name"):
        if not describe(template).parameters:
            found.append(template)
    return found


def _restrictions(
    number: WhatsAppPhoneNumber, template: MessageTemplate | None
) -> list[dict[str, str]]:
    notes = [
        {
            "source": "meta",
            "text": (
                "The recipient must have WhatsApp on that number. If it does not, Meta reports "
                "the message as failed."
            ),
        },
        {
            "source": "meta",
            "text": (
                "If this number is still in Meta's test mode, only numbers on your allowed list "
                "in Meta can receive messages. This console cannot read that list."
            ),
        },
    ]
    if template is not None and template.category.upper() == "MARKETING":
        notes.append(
            {
                "source": "meta",
                "text": "This is a marketing template: only send it to someone who has opted in.",
            }
        )
    if number.messaging_limit_tier:
        notes.append(
            {
                "source": "meta",
                "text": f"Meta reports this number's messaging limit as {number.messaging_limit_tier}.",
            }
        )
    return notes


def build_preview(
    *,
    organization: Any,
    scope: SendScope,
    phone_number_id: Any,
    contact_id: Any = None,
    to: str | None = None,
    attest_consent: bool = False,
    template_id: Any = None,
) -> Preview:
    """Everything the person must see before approving. Sends nothing, calls
    no provider. Unknown or out-of-scope ids are 404."""
    number = (
        WhatsAppPhoneNumber.objects.for_organization(organization)
        .select_related("business_account", "business")
        .filter(pk=phone_number_id)
        .first()
    )
    if number is None or not scope.permits(number.business_id):
        raise NotFoundError("phone number not found")

    blockers: list[dict[str, str]] = []

    def block(code: str, text: str) -> None:
        blockers.append({"code": code, "message": text})

    if number.status != WhatsAppPhoneNumber.Status.CONNECTED:
        block("number_not_connected", "This number is not connected, so it cannot send.")

    # ---- recipient
    contact: Contact | None = None
    phone = ""
    authorization = ""
    if contact_id:
        contact = Contact.objects.filter(pk=contact_id, business_id=number.business_id).first()
        if contact is None:
            raise NotFoundError("contact not found")
        phone = contact.phone_number or ""
        if not phone:
            block("no_phone", "This contact has no phone number.")
    elif to:
        try:
            phone = normalize_phone(to)
        except InvalidIdentifierError as exc:
            block("invalid_number", str(exc))
        if phone:
            contact = Contact.objects.filter(
                business_id=number.business_id, phone_number=phone
            ).first()
    else:
        block("recipient_required", "Choose who should receive the test message.")

    if contact is not None and contact.opt_in_status == Contact.OptInStatus.OPTED_OUT:
        block("opted_out", "This person opted out of messages. They cannot be sent a test.")
    elif contact is not None and contact.opt_in_status == Contact.OptInStatus.OPTED_IN:
        authorization = "opted_in_contact"
    elif phone:
        if attest_consent:
            authorization = "attested"
        else:
            block(
                "consent_required",
                "Confirm that this person agreed to receive a test message, or choose a contact "
                "who has opted in.",
            )

    # ---- content (server-chosen, no free text)
    template: MessageTemplate | None = None
    candidates = sendable_templates(number)
    if template_id:
        template = next((t for t in candidates if str(t.pk) == str(template_id)), None)
        if template is None:
            block(
                "template_unavailable",
                "That template is not approved, or needs variables a test cannot fill.",
            )
    elif candidates:
        template = next((t for t in candidates if t.name == "hello_world"), candidates[0])
    else:
        block(
            "no_template",
            "There is no approved template without variables. WhatsApp only allows starting a "
            "conversation with an approved template. Create or sync one on the Templates page.",
        )

    content = ""
    if template is not None:
        try:
            content = build(template, {}).rendered_body or f"[template: {template.name}]"
        except TemplateParameterError:
            block("template_unavailable", "This template needs variables a test cannot fill.")
            template = None

    cost = estimate_cost(
        message_type="template",
        category=template.category if template else "",
        recipient_phone=phone,
        provider=number.provider,
        organization_id=number.organization_id,
        business_id=number.business_id,
        business_account_id=number.business_account_id,
    )
    preview = Preview(
        can_send=not blockers,
        sender={
            "id": str(number.pk),
            "display_phone_number": number.display_phone_number,
            "label": number.label or number.verified_name or "",
            "business": number.business.name,
        },
        recipient={
            "name": (contact.display_name if contact else "") or "",
            "phone_number": phone,
        },
        message_type="template",
        content=content,
        template=(
            {
                "id": str(template.pk),
                "name": template.name,
                "language": template.language,
                "category": template.category,
            }
            if template
            else None
        ),
        cost=cost,
        restrictions=_restrictions(number, template),
        blockers=blockers,
        authorization=authorization,
    )
    preview._number = number
    preview._template = template
    preview._phone = phone
    preview._content_hash = _hash(number.pk, phone, template.pk if template else "", content)
    return preview


def preview_payload(preview: Preview) -> dict[str, Any]:
    return {
        "can_send": preview.can_send,
        "sender": preview.sender,
        "recipient": preview.recipient,
        "message_type": preview.message_type,
        "content": preview.content,
        "template": preview.template,
        "cost": preview.cost,
        "restrictions": preview.restrictions,
        "blockers": preview.blockers,
        "authorization": preview.authorization,
    }


def issue_approval(*, preview: Preview, user: Any, organization: Any) -> tuple[str, int]:
    """A single-use token that is only a *request to be asked again*: it grants
    nothing until the send call also carries ``approve: true``."""
    token = secrets.token_urlsafe(24)
    cache.set(
        _CACHE_PREFIX + token,
        {
            "user": str(user.pk),
            "org": str(organization.pk),
            "phone_number_id": str(preview._number.pk) if preview._number else "",
            "recipient": preview._phone,
            "template_id": str(preview._template.pk) if preview._template else "",
            "content_hash": preview._content_hash,
            "authorization": preview.authorization,
            "issued_at": timezone.now().isoformat(),
        },
        APPROVAL_TTL_SECONDS,
    )
    return token, APPROVAL_TTL_SECONDS


def _load(token: str, user: Any, organization: Any) -> dict[str, Any]:
    stored = cache.get(_CACHE_PREFIX + (token or ""))
    if (
        not token
        or not stored
        or stored["user"] != str(user.pk)
        or stored["org"] != str(organization.pk)
    ):
        raise ApprovalInvalidError()
    return stored


def cancel_approval(*, token: str, user: Any, organization: Any, request: Any = None) -> None:
    stored = cache.get(_CACHE_PREFIX + (token or ""))
    if stored and stored["user"] == str(user.pk) and stored["org"] == str(organization.pk):
        cache.delete(_CACHE_PREFIX + token)
        audit(
            actions.WHATSAPP_TEST_MESSAGE_CANCELLED,
            organization=organization,
            actor=user,
            metadata={"phone_number_id": stored["phone_number_id"]},
            request=request,
        )


def approve_and_send(
    *,
    token: str,
    approve: bool,
    user: Any,
    organization: Any,
    scope: SendScope,
    request: Any = None,
) -> tuple[Message, bool]:
    """Second, separate gate. Returns (message, created)."""
    from apps.messaging.services.outbound import enqueue_outbound_message

    if approve is not True:
        raise TestMessageBlockedError("You must approve this exact message before it is sent.")
    stored = _load(token, user, organization)

    # Rebuild from live data and require it to be exactly what was previewed.
    preview = build_preview(
        organization=organization,
        scope=scope,
        phone_number_id=stored["phone_number_id"],
        to=stored["recipient"],
        attest_consent=stored["authorization"] == "attested",
        template_id=stored["template_id"] or None,
    )
    if not preview.can_send:
        raise TestMessageBlockedError(
            preview.blockers[0]["message"], details={"blockers": preview.blockers}
        )
    if (
        preview._content_hash != stored["content_hash"]
        or preview.authorization != stored["authorization"]
    ):
        cache.delete(_CACHE_PREFIX + token)
        raise ApprovalInvalidError(
            "Something changed since you previewed this message. Preview it again."
        )
    if preview._number is None or preview._template is None:
        raise ApprovalInvalidError()

    key = KEY_PREFIX + hashlib.sha256(token.encode()).hexdigest()[:40]
    message, created = enqueue_outbound_message(
        organization=organization,
        request=SendRequest(
            phone_number_id=preview._number.pk,
            recipient_phone=preview._phone,
            content=TemplateSend(template_id=preview._template.pk, parameters={}),
            idempotency_key=key,
        ),
        scope=scope,
        actor=user,
        http_request=request,
    )
    cache.delete(_CACHE_PREFIX + token)
    audit(
        actions.WHATSAPP_TEST_MESSAGE_APPROVED,
        organization=organization,
        actor=user,
        resource=message,
        metadata={
            "phone_number_id": str(preview._number.pk),
            "template_id": str(preview._template.pk),
            "authorization": preview.authorization,
            "already_requested": not created,
        },
        request=request,
    )
    return message, created
