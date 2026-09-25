"""Campaign domain (Build Cycle 3).

Concepts (docs/campaign-architecture.md):

- ``Audience``          saved, business-scoped segment definition (reusable).
- ``Campaign``          what to send (number + approved template + variable
                        mapping), to whom (``audience_definition``, copied from
                        an Audience or built inline), when and how fast.
- ``CampaignRecipient`` the audience *snapshot*: one row per contact,
                        materialised once at launch. Later tag/attribute edits
                        never change who receives the campaign.
- ``CampaignEvent``     append-only lifecycle log (launched, paused, …).

A campaign message is an ordinary ``messaging.Message`` with ``campaign`` set;
it goes through the same outbound pipeline, idempotency and status webhooks
as any other send. An execution "run" is the campaign's own
started/paused/completed fields plus its events — no separate table.
"""

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.organizations.tenancy import TenantScopedModel


class Audience(TenantScopedModel):
    business = models.ForeignKey(
        "businesses.Business", on_delete=models.CASCADE, related_name="audiences"
    )
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True)
    definition = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "business", Lower("name"), name="audience_business_name_ci_unique"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Campaign(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        QUEUED = "queued", "Queued (building audience)"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"

    business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="campaigns"
    )
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=1000, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    phone_number = models.ForeignKey(
        "whatsapp.WhatsAppPhoneNumber", on_delete=models.PROTECT, related_name="campaigns"
    )
    template = models.ForeignKey(
        "templates.MessageTemplate", on_delete=models.PROTECT, related_name="campaigns"
    )
    audience = models.ForeignKey(
        Audience, on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns"
    )
    audience_definition = models.JSONField(default=dict, blank=True)
    # {"header": {...}, "body": {"1": {"source": "contact.name", "fallback": "there"}},
    #  "buttons": {"0": {...}}} — see apps/campaigns/variables.py.
    variable_mapping = models.JSONField(default=dict, blank=True)
    # Campaign send rate (messages/minute) on top of the per-number throttle.
    max_messages_per_minute = models.PositiveIntegerField(default=60)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    # IANA zone the schedule was entered in (display only; scheduled_at is UTC).
    timezone = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    audience_count = models.PositiveIntegerField(null=True, blank=True)
    snapshot_built_at = models.DateTimeField(null=True, blank=True)
    launched_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Execution heartbeat: a RUNNING campaign whose last tick is stale is
    # re-kicked by the beat watchdog (lost task / worker crash).
    last_tick_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True)
    failure_reason = models.CharField(max_length=500, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    launched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["business", "status", "-created_at"], name="campaign_biz_status_idx"
            ),
            models.Index(
                fields=["scheduled_at"],
                condition=models.Q(status="scheduled"),
                name="campaign_due_partial_idx",
            ),
            models.Index(
                fields=["last_tick_at"],
                condition=models.Q(status="running"),
                name="campaign_running_partial_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_editable(self) -> bool:
        return self.status == self.Status.DRAFT


class CampaignRecipient(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        ACCEPTED = "accepted", "Accepted by WhatsApp"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"
        CANCELLED = "cancelled", "Cancelled"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients")
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="+")
    # Snapshot of the address used (the contact may change number later).
    phone_number = models.CharField(max_length=20, blank=True)
    bsuid = models.CharField(max_length=160, blank=True)
    # Resolved template parameter values ({header, body, buttons}).
    variables = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    # Latest message for this recipient (a retry creates a new one).
    message = models.OneToOneField(
        "messaging.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaign_recipient",
    )
    # Explicit send attempts (1 = first send; +1 per operator retry). Part of
    # the idempotency key, so worker/Celery retries never create a new send.
    attempts = models.PositiveSmallIntegerField(default=0)
    skip_reason = models.CharField(max_length=64, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    failure_category = models.CharField(max_length=32, blank=True)

    class Meta:
        constraints = [
            # A contact is in a campaign's snapshot at most once, even if the
            # snapshot job is retried.
            models.UniqueConstraint(
                fields=["campaign", "contact"], name="campaign_recipient_unique"
            ),
        ]
        indexes = [
            # Counters (GROUP BY status) and worker selection of the next
            # pending recipients.
            models.Index(fields=["campaign", "status"], name="campaign_recipient_status_idx"),
            models.Index(
                fields=["campaign", "failure_category"],
                condition=models.Q(status="failed"),
                name="campaign_recipient_fail_idx",
            ),
        ]

    def idempotency_key(self) -> str:
        return f"campaign:{self.campaign_id}:{self.pk}:{self.attempts}"


class CampaignEvent(TenantScopedModel):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="events")
    event = models.CharField(max_length=40)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["campaign", "created_at"], name="campaign_event_idx")]
        ordering = ["created_at"]
