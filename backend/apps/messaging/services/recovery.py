"""Guided recovery for failed outbound messages.

Everything here is derived from what the platform stored (status, error code,
timestamps) and from the authoritative ``failure_categories`` rules. It never
calls Meta. A retry is offered only when:

- the message is outbound, FAILED and was not part of a campaign (campaigns
  have their own retry that keeps recipient accounting correct);
- its category is in ``failure_categories.RETRYABLE`` (WhatsApp certainly did
  not send it and the cause can be temporary) -- unknown and timed-out
  failures are *never* retried, because Meta has no idempotency key and a
  retry could deliver twice;
- it has not been retried already and no identical later message exists;
- it is recent, and the sending number is still connected.

The retry itself goes through ``enqueue_outbound_message`` (tenant/business
scope, service-window policy, queue, dispatch, audit) with the deterministic
idempotency key ``retry:<original id>``: the database's unique constraint on
(organization, idempotency_key) makes a second retry of the same message
impossible, even under concurrent requests.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.billing.pricing import PricingRuleNotFoundError, find_rule, market_for_phone
from apps.messaging.models import Direction, Message
from apps.messaging.services import failure_categories as fc
from apps.messaging.services.outbound import (
    MediaSend,
    SendRequest,
    TemplateSend,
)
from apps.messaging.services.retry_audit import RETRY_KEY_PREFIX
from apps.messaging.state import MessageStatus
from apps.whatsapp.models import WhatsAppPhoneNumber
from providers.whatsapp.types import TextContent

# Console rule (ours, not Meta's): older failures are not offered for retry.
RETRY_MAX_AGE = timedelta(days=7)

# Verified (resolved 2026-10-12): Meta's own destinations. We link to them, we
# do not pretend to perform their actions.
WHATSAPP_MANAGER_URL = "https://business.facebook.com/latest/whatsapp_manager"
ERROR_CODES_URL = (
    "https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes"
)

# Provider codes whose fix exists only inside Meta's tools.
_META_ONLY_CODES = frozenset(
    {
        "131048",  # sender restricted for spam reports
        "368",  # account restricted for policy violations
        "132007",  # template content violates policy
        "132015",  # template paused for quality
        "132016",  # template permanently disabled
    }
)

Kind = str  # retryable | not_retryable | unknown | external

# category -> (plain explanation, one next step)
_GUIDE: dict[str, tuple[str, str]] = {
    fc.INVALID_RECIPIENT: (
        "WhatsApp could not deliver this message to that number. It may not be on WhatsApp, "
        "may be mistyped, or cannot receive this message.",
        "Check the contact's number and consent. Sending the same message again will not help.",
    ),
    fc.PROVIDER_REJECTION: (
        "WhatsApp rejected this message.",
        "Read the reason below and correct the message or setup it points to. "
        "Sending it again unchanged would be rejected again.",
    ),
    fc.RATE_LIMIT: (
        "This message was sent faster than WhatsApp allows, so it was not sent.",
        "Retry it now. If it keeps happening, send more slowly.",
    ),
    fc.AUTHENTICATION: (
        "The connection to WhatsApp was not authorised (access token or permissions), "
        "so the message was not sent.",
        "Ask an administrator to check the WhatsApp connection first, then retry.",
    ),
    fc.TEMPLATE_ERROR: (
        "The template could not be used (not approved, wrong values or wrong language).",
        "Check this template on the Templates page, then send a new message once it is fixed.",
    ),
    fc.POLICY_WINDOW: (
        "The customer's 24-hour reply window was closed, so WhatsApp only allows an "
        "approved template.",
        "Send an approved template instead of plain text.",
    ),
    fc.TIMEOUT: (
        "WhatsApp did not confirm this message, so it may or may not have been delivered.",
        "Check the conversation first. It is not retried automatically because it could be "
        "delivered twice.",
    ),
    fc.TEMPORARY: (
        "A temporary problem on WhatsApp's side stopped this message; it was not sent.",
        "Retry it now.",
    ),
    fc.CANCELLED: (
        "This message was cancelled before it was sent.",
        "Nothing to fix. Send a new message if you still need it.",
    ),
    fc.UNKNOWN: (
        "This message failed for a reason the console does not recognise.",
        "Open the details below to see exactly what was reported. It is not retried automatically.",
    ),
}

_META_ONLY_GUIDE: dict[str, tuple[str, str]] = {
    "131048": (
        "WhatsApp restricted this number because of spam reports, so it cannot send right now.",
        "Open WhatsApp Manager in Meta, check the number's quality and any restriction, "
        "and follow Meta's steps. This cannot be fixed from this console.",
    ),
    "368": (
        "WhatsApp temporarily restricted the account for a policy violation.",
        "Open WhatsApp Manager in Meta to see the restriction and how to appeal. "
        "This cannot be fixed from this console.",
    ),
    "132007": (
        "WhatsApp says this template's content breaks its policy.",
        "Open WhatsApp Manager in Meta to review the template. This cannot be fixed from "
        "this console.",
    ),
    "132015": (
        "This template was paused by WhatsApp because of low quality.",
        "Open WhatsApp Manager in Meta to review the template. This cannot be fixed from "
        "this console.",
    ),
    "132016": (
        "This template was permanently disabled by WhatsApp.",
        "Create a new template; the disabled one cannot be re-enabled.",
    ),
}

_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-]{40,}")


def _provider_code(message: Message) -> str:
    details = message.error_details or {}
    code = str(details.get("provider_code") or "").strip()
    if not code and (message.error_code or "").isdigit():
        code = message.error_code
    return code


def safe_error_summary(message: Message) -> str:
    """What the provider reported, trimmed and with anything token-like hidden."""
    text = " ".join((message.error_message or "").split())
    text = _LONG_TOKEN.sub("[hidden]", text)
    return text if len(text) <= 300 else text[:299] + "…"


def retry_key(message_id: Any) -> str:
    return f"{RETRY_KEY_PREFIX}{message_id}"


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str


@dataclass
class Recovery:
    category: str
    label: str
    kind: Kind
    explanation: str
    next_step: str
    provider_code: str
    safe_error: str
    can_retry: bool
    blockers: list[Blocker] = field(default_factory=list)
    meta_links: list[dict[str, str]] = field(default_factory=list)
    retried_as: Message | None = None


def _links(kind: Kind) -> list[dict[str, str]]:
    links = [{"label": "Meta's error code reference", "url": ERROR_CODES_URL}]
    if kind == "external":
        links.insert(0, {"label": "Open WhatsApp Manager (Meta)", "url": WHATSAPP_MANAGER_URL})
    return links


def replacement_for(message: Message) -> Message | None:
    return (
        Message.objects.for_organization(message.organization)
        .filter(idempotency_key=retry_key(message.pk))
        .first()
    )


def _later_identical(message: Message) -> bool:
    """A later message with the same content to the same person on the same
    number that is not itself a failure: the person already has (or is about
    to get) it, so sending again would duplicate it."""
    if not message.text:
        return False
    return (
        Message.objects.for_organization(message.organization)
        .filter(
            phone_number_id=message.phone_number_id,
            contact_id=message.contact_id,
            direction=Direction.OUTBOUND,
            message_type=message.message_type,
            text=message.text,
            occurred_at__gt=message.occurred_at,
        )
        .exclude(status=MessageStatus.FAILED)
        .exclude(pk=message.pk)
        .exists()
    )


def assess(message: Message, *, now: datetime | None = None) -> Recovery:
    now = now or timezone.now()
    category = fc.categorize(message.error_code, message.error_details)
    provider_code = _provider_code(message)
    external = provider_code in _META_ONLY_CODES

    if external:
        explanation, next_step = _META_ONLY_GUIDE[provider_code]
        kind: Kind = "external"
    else:
        explanation, next_step = _GUIDE[category]
        if category in fc.RETRYABLE:
            kind = "retryable"
        elif category in {fc.UNKNOWN, fc.TIMEOUT}:
            kind = "unknown"
        else:
            kind = "not_retryable"

    blockers: list[Blocker] = []

    def block(code: str, text: str) -> None:
        blockers.append(Blocker(code, text))

    if message.direction != Direction.OUTBOUND:
        block("not_outbound", "Only messages you sent can be retried.")
    if message.status != MessageStatus.FAILED:
        block("not_failed", f"This message is now {message.get_status_display().lower()}.")
    if message.campaign_id:
        block(
            "campaign_message",
            "This message belongs to a campaign. Retry failed recipients from the campaign.",
        )
    if kind != "retryable":
        block("not_retryable", "This kind of failure is not safe to retry.")
    replacement = replacement_for(message)
    if replacement is not None:
        block("already_retried", "A retry was already requested for this message.")
    elif message.status == MessageStatus.FAILED and _later_identical(message):
        block(
            "already_resent",
            "The same message was sent to this person afterwards, so a retry would duplicate it.",
        )
    reference = message.failed_at or message.occurred_at
    if now - reference > RETRY_MAX_AGE:
        block("too_old", "This failure is more than 7 days old. Send a new message instead.")
    if message.phone_number.status != WhatsAppPhoneNumber.Status.CONNECTED:
        block("number_not_connected", "The sending number is not connected right now.")

    return Recovery(
        category=category,
        label=fc.LABELS[category],
        kind=kind,
        explanation=explanation,
        next_step=next_step,
        provider_code=provider_code,
        safe_error=safe_error_summary(message),
        can_retry=not blockers,
        blockers=blockers,
        meta_links=_links(kind),
        retried_as=replacement,
    )


# --------------------------------------------------------------------------- retry


class RetryNotAllowedError(Exception):
    def __init__(self, blockers: list[Blocker]):
        super().__init__("retry not allowed")
        self.blockers = blockers


def build_retry_request(message: Message) -> SendRequest:
    """The same content, to the same conversation, with the deterministic key."""
    content: TextContent | TemplateSend | MediaSend
    stored = message.content or {}
    if message.message_type == "text":
        content = TextContent(
            body=str(stored.get("body") or message.text),
            preview_url=bool(stored.get("preview_url")),
        )
    elif message.message_type == "template" and message.template_id:
        content = TemplateSend(
            template_id=message.template_id, parameters=dict(stored.get("parameters") or {})
        )
    else:
        attachment = message.attachments.filter(role="body").first()
        if attachment is None or attachment.media_asset_id is None:
            raise RetryNotAllowedError(
                [Blocker("content_unavailable", "The original file is no longer available.")]
            )
        content = MediaSend(
            media_asset_id=attachment.media_asset_id,
            caption=str(stored.get("caption") or "") or None,
            filename=str(stored.get("filename") or "") or None,
        )
    return SendRequest(
        content=content,
        conversation_id=message.conversation_id,
        idempotency_key=retry_key(message.pk),
        reply_to_external_id=message.reply_to_external_id or None,
    )


# --------------------------------------------------------------------------- preview


def destination(message: Message) -> dict[str, str]:
    contact = message.contact
    return {
        "name": contact.display_name or "",
        "phone_number": contact.phone_number or "",
        "from_number": message.phone_number.display_phone_number,
        "from_label": message.phone_number.label or message.phone_number.verified_name or "",
    }


def estimate_cost(
    *,
    message_type: str,
    category: str,
    recipient_phone: str,
    provider: str,
    organization_id: Any,
    business_id: Any,
    business_account_id: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Known charges for sending a message, never a guess.

    Per the verified billing model, only delivered *template* messages are
    charged; free-form messages are not. A template's amount is the loaded rate
    card's estimate (Meta's invoice is the actual charge, and utility templates
    inside an open service window are free)."""
    now = now or timezone.now()
    if message_type != "template":
        return {
            "status": "free",
            "note": "Messages that are not templates are not charged.",
        }
    market = market_for_phone(provider, recipient_phone, now) if recipient_phone else None
    if market is None or not category:
        return {"status": "unknown", "note": "No rate is loaded for this recipient's country."}
    try:
        rule = find_rule(
            provider=provider,
            market=market.code,
            category=str(category).lower(),
            at=now,
            organization_id=organization_id,
            business_id=business_id,
            business_account_id=business_account_id,
        )
    except PricingRuleNotFoundError:
        return {"status": "unknown", "note": "No rate is loaded for this template category."}
    amount: Decimal = rule.rate
    return {
        "status": "estimate",
        "amount": str(amount),
        "currency": rule.currency,
        "category": str(category).lower(),
        "note": (
            "Rate-card estimate. A template is charged only if it is delivered; "
            "Meta's invoice is the actual charge."
        ),
    }


def cost_preview(message: Message, *, now: datetime | None = None) -> dict[str, Any]:
    return estimate_cost(
        message_type=message.message_type,
        category=str((message.content or {}).get("category") or ""),
        recipient_phone=message.contact.phone_number or "",
        provider=message.provider,
        organization_id=message.organization_id,
        business_id=message.business_id,
        business_account_id=message.business_account_id,
        now=now,
    )


def message_preview_text(message: Message) -> str:
    return (message.text or "")[:500]
