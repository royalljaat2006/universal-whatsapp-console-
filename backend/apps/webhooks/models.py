"""Raw webhook storage — the durable, replayable record of every authenticated
delivery. Domain state is always *derived* from these rows by the processor,
so fixing a processor bug and replaying events repairs state."""

from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.models import BaseModel


class WebhookEvent(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        PROCESSED = "processed", "Processed"
        PARTIAL = "partial", "Processed with skipped items"
        FAILED = "failed", "Failed (will retry)"
        DEAD = "dead", "Failed permanently"

    provider = models.CharField(max_length=32)
    # One delivery can carry changes for several WABAs (and hence tenants),
    # so the raw event is not tenant-owned; ``organization_ids`` records the
    # tenants it was routed to, for tenant-scoped browsing.
    organization_ids = ArrayField(models.UUIDField(), default=list, blank=True)
    object_type = models.CharField(max_length=64, blank=True)
    # Meta deliveries carry no event id: the SHA-256 of the exact body bytes
    # deduplicates redeliveries of the same batch. Item-level idempotency is
    # enforced again by domain unique constraints (message ids, status events).
    body_sha256 = models.CharField(max_length=64)
    payload = models.JSONField()
    business_account_external_ids = ArrayField(models.CharField(max_length=64), default=list)
    phone_number_external_ids = ArrayField(models.CharField(max_length=64), default=list)
    fields = ArrayField(models.CharField(max_length=64), default=list)
    received_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    request_id = models.CharField(max_length=128, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "body_sha256"], name="webhook_event_body_unique"
            ),
        ]
        indexes = [
            # Outbox sweeper: pending/failed events due for (re)processing.
            models.Index(
                fields=["next_attempt_at"],
                condition=models.Q(status__in=["pending", "failed"]),
                name="webhook_due_partial_idx",
            ),
            models.Index(fields=["-received_at"], name="webhook_received_idx"),
            models.Index(fields=["status", "-received_at"], name="webhook_status_received_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.pk}:{self.status}"


class WebhookProcessingAttempt(BaseModel):
    """One row per processing run — the audit trail for debugging/replays."""

    class Outcome(models.TextChoices):
        PROCESSED = "processed", "Processed"
        PARTIAL = "partial", "Partial"
        RETRY = "retry", "Retry scheduled"
        FAILED = "failed", "Failed"

    event = models.ForeignKey(WebhookEvent, on_delete=models.CASCADE, related_name="attempts_log")
    attempt = models.PositiveSmallIntegerField()
    trigger = models.CharField(max_length=16, default="queue")  # queue | sweeper | replay
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, blank=True)
    items_total = models.PositiveIntegerField(default=0)
    items_applied = models.PositiveIntegerField(default=0)
    items_skipped = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    duration_ms = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
