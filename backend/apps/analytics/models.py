"""Derived analytics tables (Build Cycle 3).

Messages, status timestamps and MessageCost rows are the source of truth.
Everything here is *derived* and can be rebuilt at any time
(``manage.py rebuild_analytics``). Dashboards read only these tables.

Bucketing (docs/analytics-architecture.md):

- A message is counted in the bucket of its ``occurred_at`` (enqueue time for
  outbound, provider time for inbound) — a send cohort. Its delivered/read/
  failed outcome is counted in that same bucket when the webhook arrives
  later, so rates compare like with like.
- Daily buckets are **business-local calendar days** (Business.timezone, else
  Organization.timezone). Hourly buckets are local hours stored as aware
  datetimes.
- Every table carries organization + business + WABA + number so every read
  is filtered by the caller's business scope.
- Recomputation is per (business, local date): delete + insert inside one
  transaction under an advisory lock, so replays and late webhooks converge
  instead of double counting.
"""

from django.db import models

from apps.organizations.tenancy import TenantScopedModel


class _Dimensions(TenantScopedModel):
    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="+")
    business_account = models.ForeignKey(
        "whatsapp.WhatsAppBusinessAccount", on_delete=models.CASCADE, related_name="+"
    )
    phone_number = models.ForeignKey(
        "whatsapp.WhatsAppPhoneNumber", on_delete=models.CASCADE, related_name="+"
    )
    template = models.ForeignKey(
        "templates.MessageTemplate",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    computed_at = models.DateTimeField()

    class Meta:
        abstract = True


class _MessageCounters(_Dimensions):
    inbound = models.PositiveIntegerField(default=0)
    outbound = models.PositiveIntegerField(default=0)
    # Outbound still queued/sending at computation time (outcome unknown yet).
    in_flight = models.PositiveIntegerField(default=0)
    accepted = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    delivered = models.PositiveIntegerField(default=0)
    read = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    # Failed before WhatsApp accepted the message (validation, auth, …).
    rejected = models.PositiveIntegerField(default=0)
    templates_sent = models.PositiveIntegerField(default=0)
    media_sent = models.PositiveIntegerField(default=0)
    media_received = models.PositiveIntegerField(default=0)
    # Cost state of outbound messages (MessageCost amounts are in CostMetricDaily).
    cost_priced = models.PositiveIntegerField(default=0)
    cost_pending_rate = models.PositiveIntegerField(default=0)
    cost_free = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True


class MessageMetricDaily(_MessageCounters):
    date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "date", "phone_number", "template", "campaign"],
                name="metric_daily_unique",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["business", "date"], name="metric_daily_biz_date_idx"),
            models.Index(fields=["organization", "date"], name="metric_daily_org_date_idx"),
            models.Index(
                fields=["campaign", "date"],
                condition=models.Q(campaign__isnull=False),
                name="metric_daily_campaign_idx",
            ),
        ]


class MessageMetricHourly(_MessageCounters):
    # Start of the business-local hour (aware datetime).
    hour = models.DateTimeField()
    date = models.DateField(help_text="Business-local date of the hour (recompute key).")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "hour", "phone_number", "template", "campaign"],
                name="metric_hourly_unique",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["business", "hour"], name="metric_hourly_biz_hour_idx"),
            models.Index(fields=["business", "date"], name="metric_hourly_biz_date_idx"),
        ]


class CostMetricDaily(_Dimensions):
    """Sum of immutable MessageCost amounts per bucket, category and currency."""

    date = models.DateField()
    category = models.CharField(max_length=40)
    currency = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    messages = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "business",
                    "date",
                    "phone_number",
                    "template",
                    "campaign",
                    "category",
                    "currency",
                ],
                name="cost_metric_daily_unique",
                nulls_distinct=False,
            ),
        ]
        indexes = [models.Index(fields=["business", "date"], name="cost_daily_biz_date_idx")]


class ContactActivityDaily(TenantScopedModel):
    """One row per (day, number, contact, template?) with any message.

    Unique contacts and active conversations over a date range are distinct
    counts, which cannot be summed from daily counters; this much smaller
    table answers them without touching the message table.
    """

    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="+")
    business_account = models.ForeignKey(
        "whatsapp.WhatsAppBusinessAccount", on_delete=models.CASCADE, related_name="+"
    )
    phone_number = models.ForeignKey(
        "whatsapp.WhatsAppPhoneNumber", on_delete=models.CASCADE, related_name="+"
    )
    contact = models.ForeignKey("contacts.Contact", on_delete=models.CASCADE, related_name="+")
    template = models.ForeignKey(
        "templates.MessageTemplate",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    date = models.DateField()
    inbound = models.PositiveIntegerField(default=0)
    outbound = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "date", "phone_number", "contact", "template", "campaign"],
                name="contact_activity_daily_unique",
                nulls_distinct=False,
            ),
        ]
        indexes = [models.Index(fields=["business", "date"], name="contact_activity_biz_date_idx")]


class AnalyticsDirtyBucket(models.Model):
    """(business, UTC date) whose source data changed since the last rollup.

    Inserted with ON CONFLICT DO NOTHING from the messaging hot path (no row
    updates, so no lock contention); the aggregation worker deletes a row
    *before* recomputing it, so a change racing with the recompute re-marks
    the bucket and is picked up next run.
    """

    business_id = models.UUIDField()
    utc_date = models.DateField()
    marked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business_id", "utc_date"], name="analytics_dirty_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.business_id}:{self.utc_date}"
