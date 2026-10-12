"""Outbound messaging: accept (idempotent) -> queue -> dispatch -> track.

HTTP requests only *accept* a send (validate, persist QUEUED, enqueue); the
provider call happens in a worker. Provider calls are made outside database
transactions so a slow provider never holds row locks.

Content kinds (the browser never supplies provider payloads):
- text          ``TextContent``
- template      ``TemplateSend`` — template id + parameter values; components
                are built server-side by apps.templates.parameters
- media         ``MediaSend`` — a stored MediaAsset of the same business; it is
                uploaded to the provider at dispatch time (media ids are cached
                on the attachment for 29 days; Meta keeps uploads for 30)

What may be sent is decided by apps.messaging.policy (customer service
window), never by the client.

Duplicate-send protection:
- API level: client ``Idempotency-Key`` -> unique (organization, key);
  a reused key with a different payload is rejected (409).
- Worker level: dispatch only proceeds from QUEUED under a row lock, so a
  redelivered/duplicated task is a no-op.
- Provider level: Meta has no idempotency key, so a send whose outcome is
  unknown (timeout after the request was sent) is NEVER retried
  automatically; it fails with PROVIDER_OUTCOME_UNKNOWN for human review.
  Media *uploads* are safe to repeat (an orphan upload is harmless), so they
  are retried on any non-permanent error.

Business isolation: the sending number (directly or via the conversation),
the template and every media asset must belong to a business the caller may
access; ids supplied by the client are never trusted without that check.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.contacts.models import Contact, ContactIdentity
from apps.contacts.services import (
    ContactIdentifiers,
    normalize_phone,
    record_contact_activity,
    resolve_contact,
    validate_bsuid,
)
from apps.conversations.models import Conversation
from apps.conversations.services import get_or_create_conversation, record_outbound_activity
from apps.media import storage
from apps.media.models import MediaAsset
from apps.media.rules import OUTBOUND_KINDS
from apps.messaging import policy
from apps.messaging.models import Direction, Message, MessageAttachment, MessageType
from apps.messaging.services import events
from apps.messaging.services.error_catalog import hint
from apps.messaging.services.errors import (
    IdempotencyKeyReusedError,
    MediaNotSendableError,
    PhoneNumberNotReadyError,
    ServiceWindowClosedError,
    TemplateNotSendableError,
    TemplateParametersInvalidError,
)
from apps.messaging.services.retry_audit import record_outcome as record_retry_outcome
from apps.messaging.services.status import apply_transition
from apps.messaging.state import MessageStatus
from apps.organizations.models import Organization
from apps.templates.models import MessageTemplate
from apps.templates.parameters import TemplateParameterError, UnsupportedTemplateError, build
from apps.whatsapp.models import WhatsAppPhoneNumber
from apps.whatsapp.services import provider_for, sender_ref
from core.exceptions import ConfigurationRequiredError, DomainError, NotFoundError
from providers.whatsapp.errors import ProviderError, Retryability
from providers.whatsapp.types import (
    MediaContent,
    MediaKind,
    OutboundContent,
    OutboundMessage,
    Recipient,
    SenderRef,
    TemplateContent,
    TextContent,
)
from workers.retry import backoff_seconds

logger = logging.getLogger("messaging.lifecycle")

MAX_SEND_ATTEMPTS = 6
# QUEUED messages untouched for longer than the maximum retry backoff are
# re-dispatched (their enqueue was lost, e.g. Redis unavailable at commit).
STALE_QUEUED_AFTER = timedelta(minutes=20)
# Meta keeps uploaded media 30 days; re-upload after 29 to be safe.
PROVIDER_MEDIA_REUSE = timedelta(days=29)


class RecipientRequiredError(DomainError):
    code = "RECIPIENT_REQUIRED"
    default_message = "Provide either a conversation or a phone number and recipient."


# ---------------------------------------------------------------------------
# Request types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateSend:
    template_id: UUID
    parameters: dict[str, Any]


@dataclass(frozen=True)
class MediaSend:
    media_asset_id: UUID
    caption: str | None = None
    filename: str | None = None


SendContent = TextContent | TemplateSend | MediaSend


@dataclass(frozen=True)
class SendRequest:
    content: SendContent
    phone_number_id: UUID | None = None
    conversation_id: UUID | None = None
    recipient_phone: str | None = None
    recipient_bsuid: str | None = None
    idempotency_key: str | None = None
    reply_to_external_id: str | None = None


@dataclass(frozen=True)
class SendScope:
    """Which businesses the caller may send from (None = all in the org), and
    the business selected in the client context, if any."""

    allowed_business_ids: set[Any] | None = None
    business_id: Any | None = None

    def permits(self, business_id: Any) -> bool:
        if self.business_id is not None and business_id != self.business_id:
            return False
        return self.allowed_business_ids is None or business_id in self.allowed_business_ids


@dataclass(frozen=True)
class PreparedContent:
    message_type: str
    content: dict[str, Any]
    preview: str
    policy_kind: str
    template: MessageTemplate | None = None
    body_asset: MediaAsset | None = None
    header_asset: MediaAsset | None = None


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------


def _fingerprint(request: SendRequest) -> str:
    content = request.content
    if isinstance(content, TextContent):
        body: dict[str, Any] = {"t": "text", "body": content.body, "preview": content.preview_url}
    elif isinstance(content, TemplateSend):
        body = {"t": "template", "id": str(content.template_id), "p": content.parameters}
    else:
        body = {
            "t": "media",
            "id": str(content.media_asset_id),
            "caption": content.caption,
            "filename": content.filename,
        }
    raw = json.dumps(
        {
            "phone_number_id": str(request.phone_number_id or ""),
            "conversation_id": str(request.conversation_id or ""),
            "to": request.recipient_phone,
            "bsuid": request.recipient_bsuid,
            "content": body,
            "reply_to": request.reply_to_external_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _replay_or_conflict(organization: Organization, key: str, fingerprint: str) -> Message | None:
    existing = Message.objects.for_organization(organization).filter(idempotency_key=key).first()
    if existing and existing.request_fingerprint != fingerprint:
        raise IdempotencyKeyReusedError()
    return existing


def _resolve_target(
    organization: Organization, request: SendRequest, scope: SendScope
) -> tuple[WhatsAppPhoneNumber, Conversation | None]:
    """The sending number (and existing conversation when replying), validated
    against tenant AND business access. Unknown or foreign ids -> 404."""
    if request.conversation_id:
        conversation = (
            Conversation.objects.for_organization(organization)
            .select_related("phone_number__business_account", "phone_number__business", "contact")
            .filter(pk=request.conversation_id)
            .first()
        )
        if conversation is None or not scope.permits(conversation.business_id):
            raise NotFoundError("conversation not found")
        return conversation.phone_number, conversation
    if not request.phone_number_id or not (request.recipient_phone or request.recipient_bsuid):
        raise RecipientRequiredError()
    number = (
        WhatsAppPhoneNumber.objects.for_organization(organization)
        .select_related("business_account", "business")
        .filter(pk=request.phone_number_id)
        .first()
    )
    if number is None or not scope.permits(number.business_id):
        raise NotFoundError("phone number not found")
    return number, None


def _asset(number: WhatsAppPhoneNumber, asset_id: Any) -> MediaAsset:
    asset = MediaAsset.objects.filter(pk=asset_id, business_id=number.business_id).first()
    if asset is None:
        raise NotFoundError("media not found")
    return asset


def _prepare(number: WhatsAppPhoneNumber, content: SendContent) -> PreparedContent:
    if isinstance(content, TextContent):
        return PreparedContent(
            MessageType.TEXT,
            {"body": content.body, "preview_url": content.preview_url},
            content.body,
            policy.FREE_FORM,
        )

    if isinstance(content, MediaSend):
        asset = _asset(number, content.media_asset_id)
        if asset.kind not in OUTBOUND_KINDS:
            raise MediaNotSendableError(f"{asset.kind} files cannot be sent")
        caption = (content.caption or "").strip() or None
        if caption and asset.kind not in {MediaKind.IMAGE, MediaKind.VIDEO, MediaKind.DOCUMENT}:
            raise MediaNotSendableError(f"{asset.kind} messages do not support a caption")
        filename = (content.filename or asset.original_filename or "").strip() or None
        return PreparedContent(
            asset.kind,
            {
                "media_asset_id": str(asset.pk),
                "caption": caption,
                "filename": filename if asset.kind == MediaKind.DOCUMENT else None,
            },
            caption or f"[{asset.kind}]",
            policy.FREE_FORM,
            body_asset=asset,
        )

    template = MessageTemplate.objects.filter(
        pk=content.template_id,
        business_account_id=number.business_account_id,
        missing_from_provider=False,
    ).first()
    if template is None or not template.is_sendable:
        raise TemplateNotSendableError()
    try:
        built = build(template, content.parameters)
    except TemplateParameterError as exc:
        raise TemplateParametersInvalidError(details={"fields": exc.errors}) from exc
    except UnsupportedTemplateError as exc:
        raise TemplateNotSendableError(str(exc)) from exc
    header_asset = None
    if built.header_media_asset_id:
        header_asset = _asset(number, built.header_media_asset_id)
        expected = built.values["header"]["kind"]
        if header_asset.kind != expected:
            raise TemplateParametersInvalidError(
                details={"fields": {"header.media_asset_id": f"a {expected} file is required"}}
            )
    return PreparedContent(
        MessageType.TEMPLATE,
        {
            "template_id": str(template.pk),
            "name": template.name,
            "language": template.language,
            "category": template.category,
            "parameters": built.values,
            "components": built.components,
        },
        (built.rendered_body or f"[template:{template.name}]")[:4096],
        policy.TEMPLATE,
        template=template,
        header_asset=header_asset,
    )


def _attach(message: Message, asset: MediaAsset, role: str) -> None:
    MessageAttachment.objects.create(
        message=message,
        role=role,
        kind=asset.kind,
        media_asset=asset,
        mime_type=asset.mime_type,
        sha256=asset.sha256,
        file_size=asset.file_size,
        filename=asset.original_filename,
        caption=str(message.content.get("caption") or "") if role == "body" else "",
        storage_key=asset.storage_key,
        storage_status=MessageAttachment.StorageStatus.STORED,
    )


def enqueue_outbound_message(
    *,
    organization: Organization,
    request: SendRequest,
    scope: SendScope | None = None,
    actor: Any = None,
    http_request: Any = None,
    campaign: Any = None,
    dispatch_queue: str | None = None,
) -> tuple[Message, bool]:
    """Validate and persist an outbound message as QUEUED. Returns
    (message, created); created=False means an idempotent replay.

    ``campaign`` tags the message for campaign reporting; ``dispatch_queue``
    routes its dispatch (campaign sends use the isolated ``campaigns`` queue
    so bulk sends never delay agent replies)."""
    scope = scope or SendScope()
    phone = normalize_phone(request.recipient_phone) if request.recipient_phone else None
    bsuid = validate_bsuid(request.recipient_bsuid) if request.recipient_bsuid else None
    normalized = replace(request, recipient_phone=phone, recipient_bsuid=bsuid)
    fingerprint = _fingerprint(normalized)

    if request.idempotency_key:
        replay = _replay_or_conflict(organization, request.idempotency_key, fingerprint)
        if replay:
            if not scope.permits(replay.business_id):
                raise NotFoundError("message not found")
            return replay, False

    number, existing_conversation = _resolve_target(organization, normalized, scope)
    if number.status != WhatsAppPhoneNumber.Status.CONNECTED:
        raise PhoneNumberNotReadyError()
    prepared = _prepare(number, request.content)

    try:
        with transaction.atomic():
            if existing_conversation is not None:
                conversation = existing_conversation
                contact = conversation.contact
            else:
                contact = resolve_contact(
                    number.business,
                    ContactIdentifiers(
                        phone_number=phone,
                        bsuid=bsuid,
                        bsuid_scope=number.business_account.bsuid_scope,
                    ),
                )
                conversation, _ = get_or_create_conversation(number, contact)
            decision = policy.evaluate(conversation)
            if not decision.allows(prepared.policy_kind):
                raise ServiceWindowClosedError(details={"policy": decision.as_dict()})
            now = timezone.now()
            message = Message.objects.create(
                organization=organization,
                conversation=conversation,
                phone_number=number,
                contact=contact,
                direction=Direction.OUTBOUND,
                message_type=prepared.message_type,
                status=MessageStatus.QUEUED,
                provider=number.provider,
                content=prepared.content,
                text=prepared.preview,
                template=prepared.template,
                campaign=campaign,
                reply_to_external_id=request.reply_to_external_id or "",
                idempotency_key=request.idempotency_key,
                request_fingerprint=fingerprint,
                sent_by=actor if getattr(actor, "is_authenticated", False) else None,
                occurred_at=now,
                status_changed_at=now,
            )
            if prepared.body_asset is not None:
                _attach(message, prepared.body_asset, "body")
            if prepared.header_asset is not None:
                _attach(message, prepared.header_asset, "header")
            record_outbound_activity(conversation, at=now, preview=prepared.preview)
            record_contact_activity(contact, at=now, inbound=False)
            if actor is not None:
                audit(
                    actions.MESSAGE_SENT_MANUALLY,
                    organization=organization,
                    actor=actor,
                    resource=message,
                    metadata={
                        "message_type": prepared.message_type,
                        "phone_number_id": str(number.pk),
                        "business_id": str(number.business_id),
                        "template_id": str(prepared.template.pk) if prepared.template else None,
                    },
                    request=http_request,
                )
            events.message_created(message)
            events.conversation_updated(conversation)
            message_id = message.pk
            if dispatch_queue:
                transaction.on_commit(lambda: enqueue_dispatch(message_id, queue=dispatch_queue))
            else:
                transaction.on_commit(lambda: enqueue_dispatch(message_id))
    except IntegrityError:
        # Lost a race with an identical idempotent request.
        if request.idempotency_key:
            replay = _replay_or_conflict(organization, request.idempotency_key, fingerprint)
            if replay:
                return replay, False
        raise
    return message, True


def enqueue_dispatch(message_id: UUID, queue: str | None = None) -> None:
    from apps.messaging.tasks import send_outbound_message

    try:
        if queue:
            send_outbound_message.apply_async((str(message_id),), queue=queue)
        else:
            send_outbound_message.delay(str(message_id))
    except Exception:
        # Broker unavailable: the message stays QUEUED and the stale-queue
        # sweeper dispatches it later. Never lose or duplicate the send.
        logger.exception(
            "could not enqueue outbound dispatch", extra={"message_id": str(message_id)}
        )


# ---------------------------------------------------------------------------
# Dispatch (worker side)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchOutcome:
    """``retry_in`` set => caller should re-run dispatch after that delay."""

    status: str
    retry_in: float | None = None


class _PreparationError(Exception):
    """Provider/storage failure while preparing (uploading) media. Uploads are
    safe to repeat, so anything not permanent is retried."""

    def __init__(self, code: str, message: str, retryable: bool, details: dict[str, Any]):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details


def _throttle(number: WhatsAppPhoneNumber) -> float | None:
    if not settings.OUTBOUND_THROTTLE_ENABLED:
        return None
    from core.ratelimit import TokenBucket

    decision = TokenBucket(
        f"phone:{number.pk}", rate_per_second=number.effective_throughput_mps
    ).acquire()
    return None if decision.allowed else max(decision.retry_after_seconds, 0.05)


def _claim(message_id: UUID) -> tuple[Message | None, float | None]:
    with transaction.atomic():
        message = (
            Message.objects.select_for_update()
            .select_related("phone_number__business_account__credential")
            .filter(pk=message_id)
            .first()
        )
        if message is None or message.status != MessageStatus.QUEUED:
            return None, None
        wait = _throttle(message.phone_number)
        if wait is not None:
            return None, wait
        now = timezone.now()
        message.status = MessageStatus.SENDING
        message.status_changed_at = now
        message.sending_at = now
        message.send_attempts += 1
        message.save(
            update_fields=[
                "status",
                "status_changed_at",
                "sending_at",
                "send_attempts",
                "updated_at",
            ]
        )
        return message, None


def _fail(
    message_id: UUID,
    *,
    code: str,
    reason: str,
    details: dict[str, Any],
    provider_code: str | None = None,
) -> None:
    with transaction.atomic():
        message = Message.objects.select_for_update().get(pk=message_id)
        moved, fields = apply_transition(message, MessageStatus.FAILED, timezone.now())
        if not moved:
            return
        message.error_code = code[:64]
        message.error_message = hint(provider_code, code, reason)[:500]
        message.error_details = {**details, "provider_message": reason[:500]}
        message.save(
            update_fields=[*fields, "error_code", "error_message", "error_details", "updated_at"]
        )
        events.message_status_changed(message)
        record_retry_outcome(message)


def cancel_queued_message(message_id: UUID, *, code: str, reason: str) -> bool:
    """Fail a message that has not been handed to the provider yet (status
    QUEUED, under row lock). Returns False if dispatch already claimed it — a
    claimed send is never interrupted, its outcome is recorded normally."""
    with transaction.atomic():
        message = Message.objects.select_for_update().filter(pk=message_id).first()
        if message is None or message.status != MessageStatus.QUEUED:
            return False
        # Still holding the row lock, so dispatch cannot claim it meanwhile.
        _fail(message_id, code=code, reason=reason, details={"cancelled": True})
    return True


def _requeue(message_id: UUID) -> None:
    Message.objects.filter(pk=message_id, status=MessageStatus.SENDING).update(
        status=MessageStatus.QUEUED, status_changed_at=timezone.now(), updated_at=timezone.now()
    )


def _recipient_for(contact: Contact) -> Recipient:
    bsuid = (
        ContactIdentity.objects.filter(contact=contact, kind=ContactIdentity.Kind.BSUID)
        .values_list("value", flat=True)
        .first()
    )
    return Recipient(phone_number=contact.phone_number or None, bsuid=bsuid)


def _ensure_provider_media(
    attachment: MessageAttachment, number: WhatsAppPhoneNumber, sender: SenderRef
) -> str:
    fresh = attachment.provider_media_uploaded_at and (
        timezone.now() - attachment.provider_media_uploaded_at < PROVIDER_MEDIA_REUSE
    )
    if attachment.provider_media_id and fresh:
        return attachment.provider_media_id
    try:
        data = storage.read_bytes(attachment.storage_key)
    except storage.MediaStorageError as exc:
        raise _PreparationError("MEDIA_STORAGE_FAILED", str(exc), True, {}) from exc
    try:
        media_id = provider_for(number).upload_media(
            sender,
            data=data,
            mime_type=attachment.mime_type,
            filename=attachment.filename or "file",
        )
    except ProviderError as exc:
        raise _PreparationError(
            exc.code,
            exc.message,
            exc.retryability != Retryability.PERMANENT,
            {"provider_code": exc.provider_code, "stage": "media_upload"},
        ) from exc
    MessageAttachment.objects.filter(pk=attachment.pk).update(
        provider_media_id=media_id, provider_media_uploaded_at=timezone.now()
    )
    return media_id


def _outbound_content(message: Message, sender: SenderRef) -> OutboundContent:
    data = message.content
    number = message.phone_number
    if message.message_type == MessageType.TEXT:
        return TextContent(body=data["body"], preview_url=bool(data.get("preview_url")))
    attachments = {a.role: a for a in message.attachments.all()}
    if message.message_type == MessageType.TEMPLATE:
        components = json.loads(json.dumps(data.get("components") or []))
        header = attachments.get("header")
        for component in components:
            for param in component.get("parameters", []):
                media = param.get(param.get("type", ""))
                if isinstance(media, dict) and "asset_id" in media:
                    if header is None:
                        raise _PreparationError("MEDIA_MISSING", "header media missing", False, {})
                    param[param["type"]] = {"id": _ensure_provider_media(header, number, sender)}
        return TemplateContent(name=data["name"], language=data["language"], components=components)
    if message.message_type in OUTBOUND_KINDS:
        body = attachments.get("body")
        if body is None:
            raise _PreparationError("MEDIA_MISSING", "attachment missing", False, {})
        return MediaContent(
            kind=MediaKind(message.message_type),
            media_id=_ensure_provider_media(body, number, sender),
            caption=data.get("caption"),
            filename=data.get("filename"),
        )
    raise _PreparationError(
        "UNSUPPORTED_MESSAGE_TYPE", f"cannot send {message.message_type}", False, {}
    )


def _retry_or_fail(
    message: Message,
    code: str,
    reason: str,
    details: dict[str, Any],
    retryable: bool,
    provider_code: str | None = None,
    hint_seconds: float | None = None,
) -> DispatchOutcome:
    if retryable and message.send_attempts < MAX_SEND_ATTEMPTS:
        _requeue(message.pk)
        logger.info(
            "outbound send will be retried",
            extra={"message_id": str(message.pk), "error_code": code},
        )
        return DispatchOutcome(
            status=MessageStatus.QUEUED,
            retry_in=backoff_seconds(message.send_attempts, hint=hint_seconds),
        )
    _fail(message.pk, code=code, reason=reason, details=details, provider_code=provider_code)
    return DispatchOutcome(status=MessageStatus.FAILED)


def dispatch_outbound_message(message_id: UUID) -> DispatchOutcome:
    message, wait = _claim(message_id)
    if wait is not None:
        return DispatchOutcome(status=MessageStatus.QUEUED, retry_in=wait)
    if message is None:
        return DispatchOutcome(status="skipped")

    number = message.phone_number
    try:
        sender = sender_ref(number)
    except (ConfigurationRequiredError, PermissionError) as exc:
        _fail(message.pk, code="CONFIGURATION_REQUIRED", reason=str(exc), details={})
        return DispatchOutcome(status=MessageStatus.FAILED)

    try:
        content = _outbound_content(message, sender)
    except _PreparationError as exc:
        return _retry_or_fail(message, exc.code, exc.message, exc.details, exc.retryable)

    outbound = OutboundMessage(
        recipient=_recipient_for(message.contact),
        content=content,
        reply_to_external_id=message.reply_to_external_id or None,
    )
    try:
        result = provider_for(number).send_message(sender, outbound)
    except ProviderError as exc:
        details = {
            "provider_code": exc.provider_code,
            "http_status": exc.http_status,
            **exc.details,
        }
        code = (
            "PROVIDER_OUTCOME_UNKNOWN" if exc.retryability == Retryability.AMBIGUOUS else exc.code
        )
        return _retry_or_fail(
            message,
            code,
            exc.message,
            details,
            exc.is_retryable,
            provider_code=exc.provider_code,
            hint_seconds=exc.retry_after_seconds,
        )

    with transaction.atomic():
        locked = Message.objects.select_for_update().get(pk=message.pk)
        locked.external_id = result.external_message_id
        locked.provider_acceptance = result.acceptance.value
        locked.provider_metadata = {**locked.provider_metadata, "send_response": result.raw}
        _, fields = apply_transition(locked, MessageStatus.ACCEPTED, timezone.now())
        locked.save(
            update_fields=[
                *fields,
                "external_id",
                "provider_acceptance",
                "provider_metadata",
                "updated_at",
            ]
        )
        events.message_status_changed(locked)
    return DispatchOutcome(status=locked.status)


def fail_stuck_sending_messages() -> int:
    """A worker crash between claiming (SENDING) and recording the provider
    response leaves the outcome unknown. Such messages are failed for review,
    never resent automatically."""
    cutoff = timezone.now() - timedelta(seconds=settings.MESSAGE_SENDING_STUCK_SECONDS)
    stuck = list(
        Message.objects.filter(status=MessageStatus.SENDING, sending_at__lt=cutoff).values_list(
            "pk", flat=True
        )[:500]
    )
    for message_id in stuck:
        _fail(
            message_id,
            code="PROVIDER_OUTCOME_UNKNOWN",
            reason="Send was interrupted; the provider may or may not have delivered it.",
            details={"swept": True},
        )
    if stuck:
        logger.warning("failed stuck sending messages", extra={"count": len(stuck)})
    return len(stuck)


def requeue_stale_queued_messages() -> int:
    """Outbox recovery for sends whose enqueue was lost. Safe: dispatch claims
    under a row lock and only proceeds from QUEUED."""
    cutoff = timezone.now() - STALE_QUEUED_AFTER
    stale = list(
        Message.objects.filter(
            direction=Direction.OUTBOUND,
            status=MessageStatus.QUEUED,
            status_changed_at__lt=cutoff,
        ).values_list("pk", flat=True)[:500]
    )
    for message_id in stale:
        enqueue_dispatch(message_id)
    if stale:
        logger.warning("re-dispatched stale queued messages", extra={"count": len(stale)})
    return len(stale)
