"""Cost calculation (pure lookup + arithmetic; persistence in services).

Verified billing model (Meta pricing page, 2026-09-23):
- Charged per *delivered template* message; non-template messages and utility
  templates inside an open customer service window are free; the provider
  reports this per message (``pricing.billable`` / ``category`` / ``type``).
- Rate = f(recipient country calling code -> rate-card market, category,
  billing currency, monthly volume tier per business portfolio for utility
  and authentication).

We never infer billability ourselves and never ship rates: rate cards are
loaded as data (``manage.py load_rate_card``).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db.models import Q, QuerySet

from apps.billing.markup import charged_rate
from apps.billing.models import PricingMarket, PricingRule

REST_OF_WORLD = "*"


class PricingRuleNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CostQuote:
    rule: PricingRule | None
    currency: str
    unit_rate: Decimal
    amount: Decimal
    billable: bool
    # Meta's own rate and the markup percent that turned it into ``unit_rate``.
    base_rate: Decimal = Decimal("0")
    markup_percent: Decimal = Decimal("0")


def _effective(qs: QuerySet[Any], at: datetime) -> QuerySet[Any]:
    return qs.filter(effective_from__lte=at).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gt=at)
    )


def market_for_phone(provider: str, phone_number: str, at: datetime) -> PricingMarket | None:
    """Longest calling-code prefix match against markets effective at ``at``."""
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    prefixes = [digits[:n] for n in range(min(4, len(digits)), 0, -1)]
    if not prefixes:
        return None
    candidates = list(
        _effective(PricingMarket.objects.filter(provider=provider), at).filter(
            calling_codes__overlap=prefixes
        )
    )
    for prefix in prefixes:  # longest first
        matching = [m for m in candidates if prefix in m.calling_codes]
        if matching:
            return max(matching, key=lambda m: m.effective_from)
    return None


def find_rule(
    *,
    provider: str,
    market: str,
    category: str,
    at: datetime,
    organization_id: object = None,
    business_id: object = None,
    business_account_id: object = None,
    monthly_volume: int = 0,
) -> PricingRule:
    """Most specific rule effective at ``at``: WABA > business > organization >
    platform rate; exact market before rest-of-world; matching volume tier;
    latest start first."""
    base = _effective(
        PricingRule.objects.filter(
            provider=provider, category=category, market__in=[market.upper(), market, REST_OF_WORLD]
        ),
        at,
    )
    scoped = (
        base.filter(Q(business_account__isnull=True) | Q(business_account_id=business_account_id))
        .filter(Q(business__isnull=True) | Q(business_id=business_id))
        .filter(Q(organization__isnull=True) | Q(organization_id=organization_id))
    )
    in_tier = [
        r
        for r in scoped
        if (r.volume_tier_min is None or monthly_volume >= r.volume_tier_min)
        and (r.volume_tier_max is None or monthly_volume <= r.volume_tier_max)
    ]
    candidates = sorted(
        in_tier,
        key=lambda r: (
            r.business_account_id is not None,
            r.business_id is not None,
            r.organization_id is not None,
            r.market != REST_OF_WORLD,
            r.effective_from,
        ),
        reverse=True,
    )
    if not candidates:
        raise PricingRuleNotFoundError(f"no {provider} rule for {market}/{category} at {at}")
    return candidates[0]


def quote(
    *,
    provider: str,
    market: str,
    category: str,
    billable: bool,
    at: datetime,
    organization_id: object = None,
    business_id: object = None,
    business_account_id: object = None,
    monthly_volume: int = 0,
) -> CostQuote:
    if not billable:
        return CostQuote(
            rule=None, currency="", unit_rate=Decimal("0"), amount=Decimal("0"), billable=False
        )
    rule = find_rule(
        provider=provider,
        market=market,
        category=category,
        at=at,
        organization_id=organization_id,
        business_id=business_id,
        business_account_id=business_account_id,
        monthly_volume=monthly_volume,
    )
    rate, percent = charged_rate(rule, organization_id)
    return CostQuote(
        rule=rule,
        currency=rule.currency,
        unit_rate=rate,
        amount=rate,
        billable=True,
        base_rate=rule.rate,
        markup_percent=percent,
    )
