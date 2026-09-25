"""Per-message provider cost (public interface of the billing module).

A MessageCost is created once, when the provider has reported the message as
billable and delivered, using the rule effective at the delivery time. It is
never recalculated: loading a new rate card only affects messages priced
afterwards. Billable messages without an applicable rate stay
``cost_status=pending_rate`` until a rate card covering them is loaded
(``price_pending_messages``); no rate is ever guessed.
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import MessageCost
from apps.billing.pricing import PricingRuleNotFoundError, market_for_phone, quote
from apps.messaging.models import Message

logger = logging.getLogger(__name__)


def _recipient_digits(message: Message) -> str:
    if message.contact.phone_number:
        return message.contact.phone_number
    return ""


def _monthly_volume(message: Message, market: str, category: str, at: datetime) -> int:
    """Messages already priced this calendar month for the same business
    portfolio (falls back to the organization), market and category. Meta
    aggregates volume tiers per portfolio per month."""
    portfolio = message.phone_number.business_account.business_portfolio_external_id
    month_start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    qs = MessageCost.objects.filter(
        market=market,
        category=category,
        billable=True,
        message__delivered_at__gte=month_start,
        message__delivered_at__lt=at,
    )
    if portfolio:
        qs = qs.filter(
            message__phone_number__business_account__business_portfolio_external_id=portfolio
        )
    else:
        qs = qs.filter(organization_id=message.organization_id)
    return qs.count()


def price_message(message_id: UUID | str) -> str:
    with transaction.atomic():
        message = (
            Message.objects.select_for_update()
            .select_related("contact", "phone_number__business_account")
            .filter(pk=message_id)
            .first()
        )
        if message is None:
            return "missing"
        # Any cost outcome below changes the message's analytics bucket.
        from apps.analytics.tracking import mark_message

        mark_message(message)
        if MessageCost.objects.filter(message=message).exists():
            return Message.CostStatus.PRICED
        if message.billable is None:
            return Message.CostStatus.NONE
        if message.billable is False:
            Message.objects.filter(pk=message.pk).update(cost_status=Message.CostStatus.FREE)
            return Message.CostStatus.FREE
        delivered_at = message.delivered_at or message.read_at
        if delivered_at is None:
            return Message.CostStatus.NONE  # charged on delivery; wait for it
        category = message.pricing_category
        digits = _recipient_digits(message)
        market = market_for_phone(message.provider, digits, delivered_at) if digits else None
        if market is None or not category:
            Message.objects.filter(pk=message.pk).update(
                cost_status=Message.CostStatus.PENDING_RATE
            )
            return Message.CostStatus.PENDING_RATE
        volume = _monthly_volume(message, market.code, category, delivered_at)
        try:
            result = quote(
                provider=message.provider,
                market=market.code,
                category=category,
                billable=True,
                at=delivered_at,
                organization_id=message.organization_id,
                business_id=message.business_id,
                business_account_id=message.business_account_id,
                monthly_volume=volume,
            )
        except PricingRuleNotFoundError:
            Message.objects.filter(pk=message.pk).update(
                cost_status=Message.CostStatus.PENDING_RATE
            )
            return Message.CostStatus.PENDING_RATE
        rule = result.rule
        if rule is None:  # billable quotes always carry a rule
            return Message.CostStatus.PENDING_RATE
        snapshot: dict[str, Any] = {
            "rule_id": str(rule.pk),
            "rule_source": rule.source,
            "rule_version": rule.version,
            "market": market.code,
            "market_source": market.source,
            "category": category,
            "pricing_type": message.pricing_type,
            "currency": rule.currency,
            "unit_rate": str(rule.rate),
            "monthly_volume_before": volume,
            "tier": [rule.volume_tier_min, rule.volume_tier_max],
            "delivered_at": delivered_at.isoformat(),
        }
        try:
            with transaction.atomic():
                MessageCost.objects.create(
                    organization_id=message.organization_id,
                    message=message,
                    pricing_rule=rule,
                    provider=message.provider,
                    market=market.code,
                    category=category,
                    billable=True,
                    currency=rule.currency,
                    unit_rate=result.unit_rate,
                    amount=result.amount,
                    priced_at=timezone.now(),
                    pricing_snapshot=snapshot,
                )
        except IntegrityError:
            return Message.CostStatus.PRICED  # concurrent pricing won
        Message.objects.filter(pk=message.pk).update(cost_status=Message.CostStatus.PRICED)
        return Message.CostStatus.PRICED


def price_pending_messages(limit: int = 1000) -> dict[str, int]:
    """Retry pricing for billable messages whose rate was missing."""
    counts: dict[str, int] = {}
    ids = list(
        Message.objects.filter(cost_status=Message.CostStatus.PENDING_RATE).values_list(
            "pk", flat=True
        )[:limit]
    )
    for message_id in ids:
        outcome = price_message(message_id)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts
