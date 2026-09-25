"""Campaign report built only from persisted recipients, messages and costs."""

from decimal import Decimal
from typing import Any

from django.db.models import Count, Sum
from django.utils import timezone

from apps.billing.models import MessageCost
from apps.campaigns import progress
from apps.campaigns.models import Campaign, CampaignRecipient
from apps.messaging.models import Message
from apps.messaging.services.failure_categories import LABELS, RETRYABLE


def failures(campaign: Campaign) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for row in (
        CampaignRecipient.objects.filter(campaign=campaign, status=CampaignRecipient.Status.FAILED)
        .values("failure_category", "error_code", "error_message")
        .annotate(n=Count("id"))
        .order_by("-n")[:500]
    ):
        category = row["failure_category"] or "unknown"
        group = groups.setdefault(
            category,
            {
                "category": category,
                "label": LABELS.get(category, category),
                "retryable": category in RETRYABLE,
                "count": 0,
                "reasons": [],
            },
        )
        group["count"] += row["n"]
        group["reasons"].append(
            {"code": row["error_code"], "message": row["error_message"], "count": row["n"]}
        )
    skipped = [
        {"reason": r["skip_reason"] or "unknown", "count": r["n"]}
        for r in CampaignRecipient.objects.filter(
            campaign=campaign, status=CampaignRecipient.Status.SKIPPED
        )
        .values("skip_reason")
        .annotate(n=Count("id"))
        .order_by("-n")
    ]
    ordered = sorted(groups.values(), key=lambda g: -g["count"])
    return {
        "groups": ordered,
        "skipped": skipped,
        "retryable_recipients": sum(g["count"] for g in ordered if g["retryable"]),
    }


def cost(campaign: Campaign) -> dict[str, Any]:
    rows = (
        MessageCost.objects.filter(message__campaign=campaign)
        .values("currency")
        .annotate(amount=Sum("amount"), messages=Count("id"))
        .order_by("currency")
    )
    by_currency = []
    for r in rows:
        amount: Decimal = r["amount"] or Decimal(0)
        by_currency.append(
            {
                "currency": r["currency"],
                "amount": f"{amount.quantize(Decimal('0.000001'))}",
                "priced_messages": r["messages"],
                "average_per_message": (
                    f"{(amount / r['messages']).quantize(Decimal('0.000001'))}"
                    if r["messages"]
                    else None
                ),
            }
        )
    states = {
        r["cost_status"]: r["n"]
        for r in Message.objects.filter(campaign=campaign)
        .values("cost_status")
        .annotate(n=Count("id"))
        .order_by()
    }
    pending = states.get(Message.CostStatus.PENDING_RATE, 0)
    return {
        "state": (
            ("partial" if pending else "priced")
            if by_currency
            else ("pending_rate" if pending else "none")
        ),
        "by_currency": by_currency,
        "pending_rate_messages": pending,
        "free_messages": states.get(Message.CostStatus.FREE, 0),
        "awaiting_pricing_report": states.get(Message.CostStatus.NONE, 0),
    }


def report(campaign: Campaign) -> dict[str, Any]:
    started, finished = campaign.started_at, campaign.completed_at or campaign.cancelled_at
    end = finished or (timezone.now() if started else None)
    return {
        "campaign": {
            "id": str(campaign.pk),
            "name": campaign.name,
            "status": campaign.status,
            "business": campaign.business.name,
            "phone_number": campaign.phone_number.display_phone_number,
            "template": {
                "id": str(campaign.template_id),
                "name": campaign.template.name,
                "language": campaign.template.language,
                "category": campaign.template.category,
            },
        },
        "progress": progress.counts(campaign.pk),
        "cost": cost(campaign),
        "failures": failures(campaign),
        "timing": {
            "launched_at": campaign.launched_at.isoformat() if campaign.launched_at else None,
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "duration_seconds": int((end - started).total_seconds()) if started and end else None,
            "running": finished is None and started is not None,
        },
        "messages_total": Message.objects.filter(campaign=campaign).count(),
    }
