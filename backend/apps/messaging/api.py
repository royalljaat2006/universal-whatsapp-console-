"""Messages API.

GET  /api/v1/messages/?conversation=<id>        timeline (cursor, newest first)
POST /api/v1/messages/                          accept an outbound send (202)
GET  /api/v1/messages/<id>/                      detail
GET  /api/v1/messages/<id>/status-history/       every provider status report
GET  /api/v1/messages/failures/                  recent failures + guided recovery
GET  /api/v1/messages/<id>/recovery/             one failure: next step + retry preview
POST /api/v1/messages/<id>/retry/                queue one safe retry ({"confirm": true})

Send types (the browser sends values, never provider payloads):
- ``text``      {"text": {"body", "preview_url"?}}
- ``template``  {"template": {"template_id", "parameters": {...}}}
- ``media``     {"media": {"media_asset_id", "caption"?, "filename"?}}
Target: ``conversation_id`` (reply) or ``phone_number_id`` + ``to``/``recipient_bsuid``.
Supply an ``Idempotency-Key`` header to make retries of the POST safe.
"""

import re
from datetime import timedelta
from typing import Any

from django.db.models import Prefetch, QuerySet
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.audit import actions
from apps.audit.services import record as audit
from apps.contacts.services import InvalidIdentifierError, normalize_phone, validate_bsuid
from apps.messaging.models import Direction, Message, MessageAttachment, MessageStatusEvent
from apps.messaging.services import recovery
from apps.messaging.services.outbound import (
    MediaSend,
    SendRequest,
    SendScope,
    TemplateSend,
    enqueue_outbound_message,
)
from apps.messaging.state import MessageStatus
from apps.organizations.api.scoping import OrganizationScopedMixin
from core.api.pagination import StandardCursorPagination
from core.exceptions import DomainError, NotFoundError
from providers.whatsapp.types import TextContent

IDEMPOTENCY_HEADER = "Idempotency-Key"
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")


class AttachmentSerializer(serializers.ModelSerializer):
    media_asset_id = serializers.UUIDField(read_only=True, allow_null=True)
    content_url = serializers.SerializerMethodField()

    class Meta:
        model = MessageAttachment
        fields = [
            "id",
            "role",
            "kind",
            "mime_type",
            "file_size",
            "filename",
            "caption",
            "storage_status",
            "last_error",
            "media_asset_id",
            "content_url",
        ]
        read_only_fields = fields

    def get_content_url(self, obj: MessageAttachment) -> str | None:
        if obj.storage_status != MessageAttachment.StorageStatus.STORED or not obj.media_asset_id:
            return None
        return f"/api/v1/media/{obj.media_asset_id}/content/"


class MessageSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    business_account_id = serializers.UUIDField(read_only=True, allow_null=True)
    conversation_id = serializers.UUIDField(read_only=True)
    phone_number_id = serializers.UUIDField(read_only=True)
    contact_id = serializers.UUIDField(read_only=True)
    template_id = serializers.UUIDField(read_only=True, allow_null=True)
    attachments = AttachmentSerializer(many=True, read_only=True)
    template = serializers.SerializerMethodField()
    cost = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "business_id",
            "business_account_id",
            "conversation_id",
            "phone_number_id",
            "contact_id",
            "direction",
            "message_type",
            "status",
            "text",
            "template_id",
            "template",
            "attachments",
            "external_id",
            "reply_to_external_id",
            "occurred_at",
            "accepted_at",
            "sent_at",
            "delivered_at",
            "read_at",
            "failed_at",
            "error_code",
            "error_message",
            "billable",
            "pricing_category",
            "cost_status",
            "cost",
            "created_at",
        ]
        read_only_fields = fields

    def get_template(self, obj: Message) -> dict[str, Any] | None:
        if obj.message_type != "template":
            return None
        content = obj.content or {}
        return {
            "name": content.get("name"),
            "language": content.get("language"),
            "category": content.get("category"),
            "parameters": content.get("parameters"),
        }

    def get_cost(self, obj: Message) -> dict[str, Any] | None:
        cost = getattr(obj, "cost", None)
        if cost is None:
            return None
        return {"amount": str(cost.amount), "currency": cost.currency, "category": cost.category}


class TextBodySerializer(serializers.Serializer):
    body = serializers.CharField(max_length=4096)
    preview_url = serializers.BooleanField(default=False)


class TemplateBodySerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    parameters = serializers.DictField(required=False, default=dict)


class MediaBodySerializer(serializers.Serializer):
    media_asset_id = serializers.UUIDField()
    caption = serializers.CharField(max_length=1024, required=False, allow_blank=True)
    filename = serializers.CharField(max_length=240, required=False, allow_blank=True)


class SendMessageSerializer(serializers.Serializer):
    """Either reply in a conversation (``conversation_id``) or start from a
    number to a recipient (``phone_number_id`` + ``to``/``recipient_bsuid``)."""

    conversation_id = serializers.UUIDField(required=False)
    phone_number_id = serializers.UUIDField(required=False)
    to = serializers.CharField(required=False, allow_blank=False, max_length=32)
    recipient_bsuid = serializers.CharField(required=False, allow_blank=False, max_length=160)
    type = serializers.ChoiceField(choices=["text", "template", "media"])
    text = TextBodySerializer(required=False)
    template = TemplateBodySerializer(required=False)
    media = MediaBodySerializer(required=False)
    reply_to_external_id = serializers.CharField(required=False, max_length=255)

    def validate_to(self, value: str) -> str:
        try:
            return normalize_phone(value)
        except InvalidIdentifierError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_recipient_bsuid(self, value: str) -> str:
        try:
            return validate_bsuid(value)
        except InvalidIdentifierError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs.get("conversation_id"):
            if attrs.get("phone_number_id") or attrs.get("to") or attrs.get("recipient_bsuid"):
                raise serializers.ValidationError(
                    "conversation_id cannot be combined with phone_number_id/to/recipient_bsuid"
                )
        elif not attrs.get("phone_number_id"):
            raise serializers.ValidationError(
                "either conversation_id or phone_number_id is required"
            )
        elif not (attrs.get("to") or attrs.get("recipient_bsuid")):
            raise serializers.ValidationError("either 'to' or 'recipient_bsuid' is required")
        if not attrs.get(attrs["type"]):
            raise serializers.ValidationError({attrs["type"]: "required for this message type"})
        return attrs

    def to_request(self, idempotency_key: str | None) -> SendRequest:
        data = self.validated_data
        content: TextContent | TemplateSend | MediaSend
        if data["type"] == "text":
            content = TextContent(**data["text"])
        elif data["type"] == "template":
            content = TemplateSend(
                template_id=data["template"]["template_id"],
                parameters=data["template"].get("parameters") or {},
            )
        else:
            media = data["media"]
            content = MediaSend(
                media_asset_id=media["media_asset_id"],
                caption=media.get("caption") or None,
                filename=media.get("filename") or None,
            )
        return SendRequest(
            phone_number_id=data.get("phone_number_id"),
            conversation_id=data.get("conversation_id"),
            content=content,
            recipient_phone=data.get("to"),
            recipient_bsuid=data.get("recipient_bsuid"),
            idempotency_key=idempotency_key,
            reply_to_external_id=data.get("reply_to_external_id"),
        )


class TimelinePagination(StandardCursorPagination):
    ordering = ("-occurred_at", "-id")


def message_queryset(queryset: QuerySet[Message]) -> QuerySet[Message]:
    return queryset.select_related("cost").prefetch_related(
        Prefetch("attachments", queryset=MessageAttachment.objects.order_by("created_at"))
    )


class MessageListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Message
    business_field = "business"
    serializer_class = MessageSerializer
    pagination_class = TimelinePagination
    required_permissions = {"get": "conversations.view", "post": "messages.send"}
    throttle_scope = "send"

    def get_throttles(self) -> list[Any]:
        throttles = super().get_throttles()
        if self.request.method == "POST":
            throttles.append(ScopedRateThrottle())
        return throttles

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        conversation = self.request.query_params.get("conversation")
        if conversation:
            queryset = queryset.filter(conversation_id=conversation)
        return message_queryset(queryset)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        key = request.headers.get(IDEMPOTENCY_HEADER)
        if key is not None and not _IDEMPOTENCY_KEY.match(key):
            raise serializers.ValidationError(
                {IDEMPOTENCY_HEADER: "8-128 characters: letters, digits, . _ : -"}
            )
        payload = SendMessageSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        message, created = enqueue_outbound_message(
            organization=self.organization,
            request=payload.to_request(key),
            scope=SendScope(
                allowed_business_ids=self.allowed_business_ids,
                business_id=self.business.pk if self.business else None,
            ),
            actor=request.user,
            http_request=request,
        )
        message = message_queryset(Message.objects.filter(pk=message.pk)).get()
        return Response(
            MessageSerializer(message).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
            headers={"Idempotent-Replayed": "true"} if not created else None,
        )


class MessageDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    queryset_model = Message
    business_field = "business"
    serializer_class = MessageSerializer
    required_permissions = {"get": "conversations.view"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return message_queryset(queryset)


class StatusEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageStatusEvent
        fields = ["status", "provider_status", "occurred_at", "applied", "errors", "pricing"]
        read_only_fields = fields


class RetryNotAllowed(DomainError):
    code = "RETRY_NOT_ALLOWED"
    status_code = status.HTTP_409_CONFLICT
    default_message = "This message cannot be retried."


def _replacement_payload(replacement: Message | None) -> dict[str, Any] | None:
    """The retry's own, current state exactly as stored: never inferred."""
    if replacement is None:
        return None
    payload: dict[str, Any] = {
        "id": str(replacement.pk),
        "status": replacement.status,
        "status_label": replacement.get_status_display(),
        "created_at": replacement.created_at,
        "accepted_at": replacement.accepted_at,
        "sent_at": replacement.sent_at,
        "delivered_at": replacement.delivered_at,
        "read_at": replacement.read_at,
        "failed_at": replacement.failed_at,
        "failure": None,
    }
    if replacement.status == MessageStatus.FAILED:
        info = recovery.assess(replacement)
        payload["failure"] = {
            "category": info.category,
            "label": info.label,
            "safe_error": info.safe_error,
            "can_retry": info.can_retry,
        }
    return payload


def _recovery_payload(message: Message, info: recovery.Recovery) -> dict[str, Any]:
    contact = message.contact
    return {
        "id": str(message.pk),
        "conversation_id": str(message.conversation_id),
        "business_id": str(message.business_id),
        "message_type": message.message_type,
        "status": message.status,
        "preview": recovery.message_preview_text(message),
        "occurred_at": message.occurred_at,
        "failed_at": message.failed_at,
        "recipient": {"name": contact.display_name or "", "phone_number": contact.phone_number},
        "from_number": message.phone_number.display_phone_number,
        "recovery": {
            "category": info.category,
            "label": info.label,
            "kind": info.kind,
            "explanation": info.explanation,
            "next_step": info.next_step,
            "provider_code": info.provider_code,
            "safe_error": info.safe_error,
            "can_retry": info.can_retry,
            "blockers": [{"code": b.code, "message": b.message} for b in info.blockers],
            "links": info.meta_links,
            "retried_as": _replacement_payload(info.retried_as),
        },
    }


def _failed_messages(view: Any) -> QuerySet[Message]:
    return view.restrict_to_businesses(
        Message.objects.for_organization(view.organization), "business"
    ).select_related("contact", "phone_number", "organization")


class FailedMessagesView(OrganizationScopedMixin, APIView):
    """GET /api/v1/messages/failures/?days=7&limit=25: recent failed outbound
    messages with a plain explanation and the one safe next step."""

    required_permissions = {"get": "conversations.view"}

    def get(self, request: Request) -> Response:
        try:
            days = min(max(int(request.query_params.get("days", 7)), 1), 30)
            limit = min(max(int(request.query_params.get("limit", 25)), 1), 50)
        except ValueError as exc:
            raise serializers.ValidationError("days and limit must be whole numbers") from exc
        since = timezone.now() - timedelta(days=days)
        queryset = _failed_messages(self).filter(
            direction=Direction.OUTBOUND, status=MessageStatus.FAILED, occurred_at__gte=since
        )
        if self.business is not None:
            queryset = queryset.filter(business=self.business)
        total = queryset.count()
        rows = []
        for message in queryset.order_by("-failed_at", "-occurred_at")[:limit]:
            rows.append(_recovery_payload(message, recovery.assess(message)))
        kinds: dict[str, int] = {}
        for row in rows:
            kinds[row["recovery"]["kind"]] = kinds.get(row["recovery"]["kind"], 0) + 1
        return Response(
            {
                "generated_at": timezone.now(),
                "days": days,
                "total": total,
                "shown": len(rows),
                "kinds": kinds,
                "results": rows,
            }
        )


class MessageRecoveryView(OrganizationScopedMixin, APIView):
    """GET /api/v1/messages/<id>/recovery/: what happened, what to do, and (when
    a retry is allowed) exactly what a retry would send, to whom, and at what
    known cost."""

    required_permissions = {"get": "conversations.view"}

    def get(self, request: Request, pk: str) -> Response:
        message = _failed_messages(self).filter(pk=pk, direction=Direction.OUTBOUND).first()
        if message is None:
            raise NotFoundError("message not found")
        info = recovery.assess(message)
        payload = _recovery_payload(message, info)
        payload["message_text"] = message.text[:2000]
        payload["retry_preview"] = None
        if info.can_retry:
            payload["retry_preview"] = {
                "destination": recovery.destination(message),
                "message_text": message.text[:2000],
                "duplicate_risk": {
                    "level": "low",
                    "text": (
                        "WhatsApp did not send the original, so one retry should not "
                        "duplicate it. It is sent once; a second retry of the same message "
                        "is refused."
                    ),
                },
                "cost": recovery.cost_preview(message),
                "confirmation_required": True,
            }
        return Response(payload)


class RetryRequestSerializer(serializers.Serializer):
    confirm = serializers.BooleanField()


class MessageRetryView(OrganizationScopedMixin, APIView):
    """POST /api/v1/messages/<id>/retry/ {"confirm": true}: queue ONE retry of a
    failed message when the authoritative rules allow it. The response is the
    new message as queued; its delivery is reported later by WhatsApp."""

    required_permissions = {"post": "messages.send"}
    throttle_scope = "send"

    def get_throttles(self) -> list[Any]:
        return [*super().get_throttles(), ScopedRateThrottle()]

    def post(self, request: Request, pk: str) -> Response:
        body = RetryRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        if body.validated_data["confirm"] is not True:
            raise serializers.ValidationError({"confirm": "Confirm the retry to continue."})
        message = _failed_messages(self).filter(pk=pk, direction=Direction.OUTBOUND).first()
        if message is None:
            raise NotFoundError("message not found")

        info = recovery.assess(message)
        if not info.can_retry:
            audit(
                actions.MESSAGE_RETRY_REFUSED,
                organization=self.organization,
                actor=request.user,
                resource=message,
                metadata={
                    "category": info.category,
                    "blockers": [b.code for b in info.blockers],
                },
                request=request,
            )
            raise RetryNotAllowed(
                info.blockers[0].message,
                details={
                    "blockers": [{"code": b.code, "message": b.message} for b in info.blockers]
                },
            )

        retry, created = enqueue_outbound_message(
            organization=self.organization,
            request=recovery.build_retry_request(message),
            scope=SendScope(
                allowed_business_ids=self.allowed_business_ids,
                business_id=self.business.pk if self.business else None,
            ),
            actor=request.user,
            http_request=request,
        )
        audit(
            actions.MESSAGE_RETRY_REQUESTED,
            organization=self.organization,
            actor=request.user,
            resource=message,
            metadata={
                "retry_message_id": str(retry.pk),
                "category": info.category,
                "already_requested": not created,
            },
            request=request,
        )
        retry = message_queryset(Message.objects.filter(pk=retry.pk)).get()
        return Response(
            {
                "created": created,
                "message": MessageSerializer(retry).data,
                "status_note": (
                    "Retry queued. This only means it was accepted for sending; "
                    "WhatsApp will report whether it was delivered."
                ),
            },
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class MessageStatusHistoryView(OrganizationScopedMixin, APIView):
    required_permissions = {"get": "conversations.view"}

    def get(self, request: Request, pk: str) -> Response:
        messages = self.restrict_to_businesses(
            Message.objects.for_organization(self.organization), "business"
        )
        message = messages.filter(pk=pk).first()
        if message is None:
            raise NotFoundError("message not found")
        events = MessageStatusEvent.objects.filter(message=message).order_by("occurred_at")[:100]
        return Response(StatusEventSerializer(events, many=True).data)
