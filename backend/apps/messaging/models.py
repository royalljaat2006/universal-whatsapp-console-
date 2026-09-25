from typing import Any

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models.functions import Upper

from apps.messaging.state import MessageStatus
from apps.organizations.tenancy import TenantScopedModel


class Direction(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"


class MessageType(models.TextChoices):
    TEXT = "text", "Text"
    TEMPLATE = "template", "Template"
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    AUDIO = "audio", "Audio"
    DOCUMENT = "document", "Document"
    STICKER = "sticker", "Sticker"
    INTERACTIVE = "interactive", "Interactive"
    BUTTON = "button", "Button reply"
    REACTION = "reaction", "Reaction"
    LOCATION = "location", "Location"
    CONTACTS = "contacts", "Contacts"
    ORDER = "order", "Order"
    SYSTEM = "system", "System"
    UNSUPPORTED = "unsupported", "Unsupported"


class Message(TenantScopedModel):
    business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="messages"
    )
    conversation = models.ForeignKey(
        "conversations.Conversation", on_delete=models.PROTECT, related_name="messages"
    )
    # Denormalised sender WABA for analytics/cost grouping (from phone_number).
    business_account = models.ForeignKey(
        "whatsapp.WhatsAppBusinessAccount",
        on_delete=models.PROTECT,
        related_name="messages",
        null=True,
        blank=True,
    )
    # Campaign-ready identifier (set by the campaign engine in a later cycle).
    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    phone_number = models.ForeignKey(
        "whatsapp.WhatsAppPhoneNumber", on_delete=models.PROTECT, related_name="messages"
    )
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.PROTECT, related_name="messages"
    )
    direction = models.CharField(max_length=8, choices=Direction.choices)
    message_type = models.CharField(max_length=16, choices=MessageType.choices)
    status = models.CharField(max_length=16, choices=MessageStatus.choices)

    provider = models.CharField(max_length=32, default="meta")
    # Provider message id (Meta "wamid..."). Null until the provider accepts
    # an outbound message.
    # NULL (not "") so the partial unique constraint ignores unsent messages.
    external_id = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001
    provider_acceptance = models.CharField(max_length=40, blank=True)

    # Normalised, type-specific body, e.g. {"body": "..."} for text or the
    # template name/language/components for templates.
    content = models.JSONField(default=dict)
    # Plain-text rendering for previews and search (text body / caption).
    text = models.TextField(blank=True)
    reply_to_external_id = models.CharField(max_length=255, blank=True)
    # Raw provider data: the inbound message object from the webhook, or the
    # provider's send response for outbound. Kept for debugging and for types
    # added later (media, interactive) without re-fetching.
    provider_metadata = models.JSONField(default=dict, blank=True)
    template = models.ForeignKey(
        "templates.MessageTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )

    # Outbound request idempotency (client-supplied Idempotency-Key header).
    idempotency_key = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    request_fingerprint = models.CharField(max_length=64, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # Provider-reported (inbound) or enqueue time (outbound).
    occurred_at = models.DateTimeField()
    status_changed_at = models.DateTimeField()
    sending_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    send_attempts = models.PositiveSmallIntegerField(default=0)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    error_details = models.JSONField(default=dict, blank=True)

    # Billing signals as reported by the provider (costs computed in billing).
    billable = models.BooleanField(null=True, blank=True)
    pricing_category = models.CharField(max_length=40, blank=True)
    pricing_type = models.CharField(max_length=40, blank=True)

    class CostStatus(models.TextChoices):
        NONE = "none", "No pricing report yet"
        FREE = "free", "Not billable (per provider)"
        PENDING_RATE = "pending_rate", "Billable; no applicable rate loaded"
        PRICED = "priced", "Priced"

    cost_status = models.CharField(
        max_length=16, choices=CostStatus.choices, default=CostStatus.NONE
    )

    class Meta:
        constraints = [
            # Provider ids are unique per sender and direction. Two of OUR
            # numbers messaging each other would see the same wamid once as
            # outbound (sender) and once as inbound (receiver), so a global
            # unique on external_id would be wrong.
            models.UniqueConstraint(
                fields=["phone_number", "direction", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="message_provider_id_unique",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="message_idempotency_key_unique",
            ),
        ]
        indexes = [
            # Conversation timeline (the hottest read path).
            models.Index(
                fields=["organization", "conversation", "-occurred_at"],
                name="msg_org_conv_occurred_idx",
            ),
            models.Index(fields=["organization", "-created_at"], name="msg_org_created_idx"),
            models.Index(
                fields=["organization", "phone_number", "-created_at"],
                name="msg_org_phone_created_idx",
            ),
            models.Index(
                fields=["organization", "status", "-created_at"],
                name="msg_org_status_created_idx",
            ),
            # Message-content search (icontains -> UPPER(text) LIKE ...).
            GinIndex(OpClass(Upper("text"), name="gin_trgm_ops"), name="msg_text_trgm_idx"),
            # Analytics recompute reads one business-local day at a time.
            models.Index(fields=["business", "occurred_at"], name="msg_business_occurred_idx"),
            # Campaign reports / failure views.
            models.Index(
                fields=["campaign", "status"],
                condition=models.Q(campaign__isnull=False),
                name="msg_campaign_status_idx",
            ),
            # Sweeper for sends interrupted by a crash; tiny partial index.
            models.Index(
                fields=["sending_at"],
                condition=models.Q(status="sending"),
                name="msg_sending_partial_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.direction}:{self.message_type}:{self.pk}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # The business is always the conversation's business (denormalised
        # for business-scoped queries); never trust a caller-supplied value.
        self.business_id = self.conversation.business_id
        self.business_account_id = self.phone_number.business_account_id
        super().save(*args, **kwargs)


class MessageStatusEvent(TenantScopedModel):
    """Append-only history of every provider status report for a message."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="status_events")
    status = models.CharField(max_length=16, choices=MessageStatus.choices, blank=True)
    provider_status = models.CharField(max_length=40)
    occurred_at = models.DateTimeField()
    applied = models.BooleanField(help_text="Whether this event changed the message status.")
    errors = models.JSONField(default=list, blank=True)
    pricing = models.JSONField(null=True, blank=True)
    # Raw webhook event id (webhooks.WebhookEvent) — kept as a plain UUID to
    # avoid a model-level dependency from messaging onto the ingestion layer.
    source_event_id = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "provider_status", "occurred_at"],
                name="message_status_event_unique",
            ),
        ]
        ordering = ["occurred_at"]


class MessageAttachment(TenantScopedModel):
    """Media metadata. Binary content goes to object storage (never Postgres);
    downloading from the provider is NOT IMPLEMENTED in this phase."""

    class StorageStatus(models.TextChoices):
        PENDING = "pending", "Pending download"
        STORED = "stored", "Stored"
        FAILED = "failed", "Failed"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=16)
    provider_media_id = models.CharField(max_length=128, blank=True)
    mime_type = models.CharField(max_length=128, blank=True)
    sha256 = models.CharField(max_length=128, blank=True)
    file_size = models.PositiveBigIntegerField(null=True, blank=True)
    filename = models.CharField(max_length=255, blank=True)
    caption = models.TextField(blank=True)
    storage_key = models.CharField(max_length=512, blank=True)
    storage_status = models.CharField(
        max_length=16, choices=StorageStatus.choices, default=StorageStatus.PENDING
    )
    # Stored file (inbound: after download; outbound: the uploaded asset).
    media_asset = models.ForeignKey(
        "media.MediaAsset", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Role inside the message: the message body media, or a template header.
    role = models.CharField(max_length=16, default="body")
    provider_media_uploaded_at = models.DateTimeField(null=True, blank=True)
    download_attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.organization_id = self.message.organization_id
        super().save(*args, **kwargs)
