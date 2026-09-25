"""Builders for Cloud API request bodies (POST /{phone-number-id}/messages).

Shapes verified against the official Messages API reference; see
docs/integrations/meta/api-assumptions.md#send-message.
"""

from typing import Any

from providers.whatsapp.errors import ProviderCapabilityError
from providers.whatsapp.types import (
    MediaContent,
    MediaKind,
    OutboundMessage,
    ReactionContent,
    Recipient,
    TemplateContent,
    TextContent,
)

_CAPTION_KINDS = {MediaKind.IMAGE, MediaKind.VIDEO, MediaKind.DOCUMENT}


def _recipient_fields(recipient: Recipient) -> dict[str, str]:
    # Phone number takes precedence at Meta when both are given; send exactly
    # one identifier to keep behaviour explicit.
    if recipient.phone_number:
        return {"to": recipient.phone_number}
    if not recipient.bsuid:
        raise ProviderCapabilityError("recipient has neither phone number nor BSUID")
    return {"recipient": recipient.bsuid}


def _content_fields(message: OutboundMessage) -> dict[str, Any]:
    content = message.content
    if isinstance(content, TextContent):
        return {"type": "text", "text": {"body": content.body, "preview_url": content.preview_url}}
    if isinstance(content, TemplateContent):
        template: dict[str, Any] = {"name": content.name, "language": {"code": content.language}}
        if content.components:
            template["components"] = content.components
        return {"type": "template", "template": template}
    if isinstance(content, MediaContent):
        media: dict[str, Any] = (
            {"id": content.media_id} if content.media_id else {"link": content.link}
        )
        if content.caption and content.kind in _CAPTION_KINDS:
            media["caption"] = content.caption
        if content.filename and content.kind == MediaKind.DOCUMENT:
            media["filename"] = content.filename
        return {"type": content.kind.value, content.kind.value: media}
    if isinstance(content, ReactionContent):
        return {
            "type": "reaction",
            "reaction": {"message_id": content.message_external_id, "emoji": content.emoji},
        }
    raise ProviderCapabilityError(f"unsupported content type {type(content).__name__}")


def build_send_payload(message: OutboundMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        **_recipient_fields(message.recipient),
        **_content_fields(message),
    }
    if message.reply_to_external_id:
        payload["context"] = {"message_id": message.reply_to_external_id}
    return payload


def build_mark_read_payload(external_message_id: str) -> dict[str, Any]:
    return {"messaging_product": "whatsapp", "status": "read", "message_id": external_message_id}
