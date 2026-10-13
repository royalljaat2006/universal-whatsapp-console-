"""Optional pricing adjustment on top of the platform rate card.

The behaviour is configuration, not code: nothing here knows the percentage or
which domains are exempt (both come from settings, i.e. the deployment
environment). With the defaults it does nothing.

- The rate card holds the provider's published rates, unmodified.
- An organization whose active *owner* has a **verified** email in one of the
  configured exempt domains pays the platform rate as it is.
- Every other organization pays the platform rate plus
  ``PRICING_MARKUP_PERCENT`` percent.
- A negotiated rule (organization, business or WABA specific) is deliberately
  never adjusted: whoever wrote that rule already decided the number.

The adjustment is applied when a message is priced, never written into the rate
card, so loading the provider's next rate card needs nothing else, new
organizations need no rules, and every ``MessageCost`` snapshot records the
provider's own rate, the percentage and why, so a cost can always be explained and
reconciled.

The exemption needs a verified email so that nobody can obtain the base rate by
registering with an address they do not control.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache

from apps.billing.models import PricingRule

_CACHE_SECONDS = 300
_QUANTUM = Decimal("0.000001")  # PricingRule.rate has 6 decimal places


def _exempt_domains() -> list[str]:
    return [d for d in getattr(settings, "PRICING_MARKUP_EXEMPT_EMAIL_DOMAINS", []) if d]


def is_exempt(organization_id: Any) -> bool:
    """True when an active owner of the organization has a verified email in an exempt domain."""
    from apps.organizations.models import Membership

    domains = _exempt_domains()
    if not domains or organization_id is None:
        return False
    key = f"pricing-exempt:{organization_id}:{','.join(domains)}"
    cached = cache.get(key)
    if cached is not None:
        return bool(cached)
    owners = Membership.objects.filter(
        organization_id=organization_id,
        status=Membership.Status.ACTIVE,
        role__key="owner",
        user__is_email_verified=True,
    ).values_list("user__email", flat=True)
    exempt = any(
        (email or "").lower().endswith(f"@{domain}") for email in owners for domain in domains
    )
    cache.set(key, exempt, _CACHE_SECONDS)
    return exempt


def markup_percent_for(organization_id: Any) -> Decimal:
    percent = Decimal(str(getattr(settings, "PRICING_MARKUP_PERCENT", 0) or 0))
    if percent <= 0 or is_exempt(organization_id):
        return Decimal("0")
    return percent


def charged_rate(rule: PricingRule, organization_id: Any) -> tuple[Decimal, Decimal]:
    """(rate to charge, markup percent applied) for ``rule`` and this organization."""
    negotiated = (
        rule.organization_id is not None
        or rule.business_id is not None
        or rule.business_account_id is not None
    )
    if negotiated:
        return rule.rate, Decimal("0")
    percent = markup_percent_for(organization_id)
    if percent == 0:
        return rule.rate, Decimal("0")
    marked_up = (rule.rate * (Decimal(100) + percent) / Decimal(100)).quantize(
        _QUANTUM, rounding=ROUND_HALF_UP
    )
    return marked_up, percent


def explain(rule: PricingRule, organization_id: Any) -> dict[str, str]:
    """Snapshot fields that make a charged rate explainable."""
    rate, percent = charged_rate(rule, organization_id)
    if rule.organization_id or rule.business_id or rule.business_account_id:
        reason = "negotiated rule (no markup)"
    elif percent == 0:
        reason = "exempt organization" if is_exempt(organization_id) else "markup disabled"
    else:
        reason = "platform rate plus markup"
    return {
        "meta_rate": str(rule.rate),
        "markup_percent": str(percent),
        "charged_rate": str(rate),
        "markup_reason": reason,
    }
