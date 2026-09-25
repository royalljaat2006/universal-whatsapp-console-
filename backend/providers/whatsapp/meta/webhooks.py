"""Meta webhook adapter: handshake, signature verification, normalisation.

Payload structure verified against the official webhook references (see
docs/integrations/meta/webhooks.md):

    {"object": "whatsapp_business_account",
     "entry": [{"id": <WABA_ID>, "changes": [{"field": "...", "value": {...}}]}]}

``messages`` changes carry ``metadata.phone_number_id`` plus ``messages[]``
(inbound) and/or ``statuses[]`` (outbound lifecycle). Other fields (template
status, quality, account updates) are WABA-scoped.
"""

import hashlib
import hmac
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from providers.whatsapp.base import WebhookAdapter
from providers.whatsapp.errors import WebhookVerificationError
from providers.whatsapp.meta.config import MetaConfig
from providers.whatsapp.types import (
    AccountEvent,
    ContactProfile,
    InboundMessageEvent,
    ProviderErrorDetail,
    ProviderEvent,
    StatusUpdateEvent,
    TemplateStatusEvent,
)

logger = logging.getLogger("providers.meta.webhooks")

SIGNATURE_HEADER = "X-Hub-Signature-256"
EXPECTED_OBJECT = "whatsapp_business_account"
_MEDIA_TYPES = {"image", "video", "audio", "document", "sticker"}


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _text_of(message: Mapping[str, Any]) -> str:
    kind = message.get("type")
    body = message.get(kind) if isinstance(kind, str) else None
    if kind == "text" and isinstance(body, Mapping):
        return str(body.get("body") or "")
    if kind in _MEDIA_TYPES and isinstance(body, Mapping):
        return str(body.get("caption") or "")
    if kind == "button" and isinstance(body, Mapping):
        return str(body.get("text") or "")
    if kind == "interactive" and isinstance(body, Mapping):
        for key in ("button_reply", "list_reply"):
            reply = body.get(key)
            if isinstance(reply, Mapping):
                return str(reply.get("title") or "")
    return ""


class MetaWebhookAdapter(WebhookAdapter):
    name = "meta"

    def __init__(self, config: MetaConfig):
        self._config = config

    # -- authenticity ------------------------------------------------------
    def verify_subscription(self, params: Mapping[str, str]) -> str | None:
        token = self._config.webhook_verify_token
        if not token:
            return None
        if params.get("hub.mode") != "subscribe":
            return None
        if not hmac.compare_digest(str(params.get("hub.verify_token", "")), token):
            return None
        challenge = params.get("hub.challenge")
        return str(challenge) if challenge else None

    def verify_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> None:
        secret = self._config.app_secret
        if not secret:
            raise WebhookVerificationError("META_APP_SECRET is not configured")
        header = headers.get(SIGNATURE_HEADER, "")
        if not header.startswith("sha256="):
            raise WebhookVerificationError("missing or malformed signature header")
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(header.removeprefix("sha256="), expected):
            raise WebhookVerificationError("signature mismatch")

    # -- routing -------------------------------------------------------------
    def routing_keys(self, payload: Mapping[str, Any]) -> dict[str, list[str]]:
        accounts: list[str] = []
        numbers: list[str] = []
        fields: list[str] = []
        for entry in payload.get("entry") or []:
            if entry.get("id") is not None:
                accounts.append(str(entry["id"]))
            for change in entry.get("changes") or []:
                fields.append(str(change.get("field") or ""))
                number = ((change.get("value") or {}).get("metadata") or {}).get("phone_number_id")
                if number:
                    numbers.append(str(number))
        return {
            "business_accounts": sorted(set(accounts)),
            "phone_numbers": sorted(set(numbers)),
            "fields": sorted(set(fields)),
        }

    # -- normalisation -------------------------------------------------------
    def parse(self, payload: Mapping[str, Any]) -> list[ProviderEvent]:
        if payload.get("object") != EXPECTED_OBJECT:
            logger.warning("unexpected webhook object", extra={"object": payload.get("object")})
            return []
        events: list[ProviderEvent] = []
        for entry in payload.get("entry") or []:
            waba_id = str(entry.get("id") or "")
            entry_time = _ts(entry.get("time"))
            for change in entry.get("changes") or []:
                field_name = change.get("field")
                value = change.get("value") or {}
                try:
                    if field_name == "messages":
                        events.extend(self._parse_messages_change(waba_id, value))
                    elif field_name == "message_template_status_update":
                        events.append(self._parse_template_status(waba_id, value, entry_time))
                    elif field_name:
                        events.append(
                            AccountEvent(
                                business_account_external_id=waba_id,
                                field_name=str(field_name),
                                value=dict(value),
                                occurred_at=entry_time,
                            )
                        )
                except (KeyError, TypeError, ValueError):
                    # One malformed change must not drop the rest of the batch.
                    logger.exception(
                        "malformed webhook change skipped", extra={"field": field_name}
                    )
        return events

    def _parse_messages_change(self, waba_id: str, value: Mapping[str, Any]) -> list[ProviderEvent]:
        metadata = value.get("metadata") or {}
        phone_id = str(metadata.get("phone_number_id") or "")
        if not phone_id:
            raise ValueError("messages change without metadata.phone_number_id")

        profiles: dict[str, ContactProfile] = {}
        for contact in value.get("contacts") or []:
            profile = ContactProfile(
                phone_number=contact.get("wa_id") or None,
                bsuid=contact.get("user_id") or None,
                parent_bsuid=contact.get("parent_user_id") or None,
                name=str((contact.get("profile") or {}).get("name") or ""),
            )
            for key in (profile.phone_number, profile.bsuid):
                if key:
                    profiles[key] = profile

        events: list[ProviderEvent] = []
        for message in value.get("messages") or []:
            events.append(self._parse_inbound(waba_id, phone_id, message, profiles))
        for status in value.get("statuses") or []:
            events.append(self._parse_status(waba_id, phone_id, status))
        return events

    def _parse_inbound(
        self,
        waba_id: str,
        phone_id: str,
        message: Mapping[str, Any],
        profiles: Mapping[str, ContactProfile],
    ) -> InboundMessageEvent:
        phone = message.get("from") or None
        bsuid = message.get("from_user_id") or None
        known = profiles.get(phone or "") or profiles.get(bsuid or "")
        sender = ContactProfile(
            phone_number=phone or (known.phone_number if known else None),
            bsuid=bsuid or (known.bsuid if known else None),
            parent_bsuid=message.get("from_parent_user_id")
            or (known.parent_bsuid if known else None),
            name=known.name if known else "",
        )
        if not (sender.phone_number or sender.bsuid):
            raise ValueError("inbound message without sender identity")
        kind = str(message.get("type") or "unsupported")
        body = message.get(kind)
        occurred_at = _ts(message.get("timestamp"))
        if occurred_at is None:
            raise ValueError("inbound message without valid timestamp")
        return InboundMessageEvent(
            phone_number_external_id=phone_id,
            business_account_external_id=waba_id,
            external_message_id=str(message["id"]),
            sender=sender,
            message_type=kind,
            occurred_at=occurred_at,
            content=dict(body) if isinstance(body, Mapping) else {"value": body},
            text=_text_of(message),
            reply_to_external_id=(message.get("context") or {}).get("id"),
            media=dict(body) if kind in _MEDIA_TYPES and isinstance(body, Mapping) else None,
            raw=dict(message),
        )

    def _parse_status(
        self, waba_id: str, phone_id: str, status: Mapping[str, Any]
    ) -> StatusUpdateEvent:
        occurred_at = _ts(status.get("timestamp"))
        if occurred_at is None:
            raise ValueError("status without valid timestamp")
        errors = tuple(
            ProviderErrorDetail(
                code=str(err.get("code", "")),
                title=str(err.get("title") or ""),
                message=str(err.get("message") or ""),
                details=str((err.get("error_data") or {}).get("details") or ""),
            )
            for err in status.get("errors") or []
        )
        return StatusUpdateEvent(
            phone_number_external_id=phone_id,
            business_account_external_id=waba_id,
            external_message_id=str(status["id"]),
            status=str(status["status"]),
            occurred_at=occurred_at,
            recipient=ContactProfile(
                phone_number=status.get("recipient_id") or None,
                bsuid=status.get("recipient_user_id") or None,
                parent_bsuid=status.get("recipient_parent_user_id") or None,
            ),
            errors=errors,
            pricing=dict(status["pricing"]) if status.get("pricing") else None,
            conversation=dict(status["conversation"]) if status.get("conversation") else None,
        )

    def _parse_template_status(
        self, waba_id: str, value: Mapping[str, Any], occurred_at: datetime | None
    ) -> TemplateStatusEvent:
        return TemplateStatusEvent(
            business_account_external_id=waba_id,
            template_external_id=str(value["message_template_id"]),
            template_name=str(value.get("message_template_name") or ""),
            language=str(value.get("message_template_language") or ""),
            event=str(value.get("event") or ""),
            reason=str(value.get("reason") or ""),
            category=str(value.get("message_template_category") or ""),
            occurred_at=occurred_at,
            raw=dict(value),
        )
