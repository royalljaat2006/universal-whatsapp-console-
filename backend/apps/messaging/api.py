"""Messages API.

GET  /api/v1/messages/?conversation=<id>        timeline (cursor, newest first)
POST /api/v1/messages/                          accept an outbound send (202)
GET  /api/v1/messages/<id>/                      detail
GET  /api/v1/messages/<id>/status-history/       every provider status report

Send types (the browser sends values, never provider payloads):
- ``text``      {"text": {"body", "preview_url"?}}
- ``template``  {"template": {"template_id", "parameters": {...}}}
- ``media``     {"media": {"media_asset_id", "caption"?, "filename"?}}
Target: ``conversation_id`` (reply) or ``phone_number_id`` + ``to``/``recipient_bsuid``.
Supply an ``Idempotency-Key`` header to make retries of the POST safe.
"""

import re
from typing import Any

from django.db.models import Prefetch, QuerySet
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.contacts.services import InvalidIdentifierError, normalize_phone, validate_bsuid
from apps.messaging.models import Message, MessageAttachment, MessageStatusEvent
from apps.messaging.services.outbound import (
    MediaSend,
    SendRequest,
    SendScope,
    TemplateSend,
    enqueue_outbound_message,
)
from apps.organizations.api.scoping import OrganizationScopedMixin
from core.api.pagination import StandardCursorPagination
from core.exceptions import NotFoundError
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
