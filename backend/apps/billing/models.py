"""Provider usage cost (what Meta charges us) — NOT SaaS subscription billing.

Those are separate financial domains (ADR-008). This module covers the
first; subscription billing will live in its own module when needed.

Pricing is data, never code: rates change (Meta revises rate cards and moved
to per-message pricing on 2025-07-01), so costs are computed from effective-
dated PricingRule rows and frozen in immutable MessageCost rows. Changing a
rule never rewrites history.
"""

from decimal import Decimal
from typing import Any

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from core.models import BaseModel


class PricingMarket(BaseModel):
    """Rate-card market (a country, or a "Rest of …" region) and the recipient
    country calling codes it covers. Meta bills by the recipient's calling code,
    grouped into markets per rate card; loaded from Meta's published rate card,
    never hard-coded."""

    provider = models.CharField(max_length=32)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=120)
    calling_codes = ArrayField(models.CharField(max_length=8), default=list)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=200)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "code", "effective_from"], name="pricing_market_unique"
            ),
        ]
        indexes = [GinIndex(fields=["calling_codes"], name="pricing_market_codes_gin")]


class PricingRule(BaseModel):
    class BillingUnit(models.TextChoices):
        PER_MESSAGE = "per_message", "Per delivered message"

    # Null organization = platform-wide rate card; a row with an organization
    # overrides it for that tenant (negotiated rates).
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    # Narrower overrides (negotiated/partner rates) for a business or WABA.
    business = models.ForeignKey(
        "businesses.Business", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    business_account = models.ForeignKey(
        "whatsapp.WhatsAppBusinessAccount",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    provider = models.CharField(max_length=32)
    # Rate-card market code (PricingMarket.code: a country or a "Rest of"
    # region), or "*" for a catch-all.
    market = models.CharField(max_length=64)
    # Provider pricing category, verbatim (e.g. marketing, utility,
    # authentication, authentication-international, service).
    category = models.CharField(max_length=40)
    currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=14, decimal_places=6)
    billing_unit = models.CharField(
        max_length=16, choices=BillingUnit.choices, default=BillingUnit.PER_MESSAGE
    )
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    # Monthly volume tier (messages per portfolio, market and category), when
    # the rate card is tiered. Null bounds = open-ended.
    volume_tier_min = models.PositiveIntegerField(null=True, blank=True)
    volume_tier_max = models.PositiveIntegerField(null=True, blank=True)
    source = models.CharField(max_length=200, blank=True, help_text="Rate card name/URL")
    version = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rate__gte=0), name="pricing_rate_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="pricing_valid_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["provider", "market", "category", "effective_from"],
                name="pricing_lookup_idx",
            ),
        ]


class MessageCost(BaseModel):
    """Immutable cost snapshot for one message. Created once; never updated."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="+"
    )
    message = models.OneToOneField(
        "messaging.Message", on_delete=models.PROTECT, related_name="cost"
    )
    pricing_rule = models.ForeignKey(PricingRule, on_delete=models.PROTECT, null=True, blank=True)
    provider = models.CharField(max_length=32)
    market = models.CharField(max_length=64)
    category = models.CharField(max_length=40)
    billable = models.BooleanField()
    currency = models.CharField(max_length=3)
    # Rate copied from the rule at computation time (the snapshot).
    unit_rate = models.DecimalField(max_digits=14, decimal_places=6)
    amount = models.DecimalField(max_digits=14, decimal_places=6)
    priced_at = models.DateTimeField()
    # Frozen copy of the rule and inputs used, so history survives rule edits.
    pricing_snapshot = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "priced_at"], name="cost_org_priced_idx"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise TypeError("MessageCost is immutable")
        super().save(*args, **kwargs)

    @property
    def is_free(self) -> bool:
        return not self.billable or self.amount == Decimal("0")
