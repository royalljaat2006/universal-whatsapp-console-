"""Read side of analytics: every query reads the derived tables only.

Callers pass an ``AnalyticsScope`` whose business ids come from the
authenticated membership (never from the browser), plus optional validated
WABA / number / template / campaign filters.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Count, Max, Min, QuerySet, Sum

from apps.analytics.aggregation import COUNTER_FIELDS
from apps.analytics.models import (
    AnalyticsDirtyBucket,
    ContactActivityDaily,
    CostMetricDaily,
    MessageMetricDaily,
    MessageMetricHourly,
)
from apps.analytics.tracking import day_bounds
from apps.conversations.models import Conversation
from apps.messaging.models import Direction, Message
from apps.messaging.services.failure_categories import LABELS, RETRYABLE, categorize
from apps.messaging.state import MessageStatus
from apps.templates.models import MessageTemplate
from apps.whatsapp.models import WhatsAppPhoneNumber

MAX_RANGE_DAYS = 400
MAX_HOURLY_DAYS = 7


@dataclass(frozen=True)
class AnalyticsScope:
    business_ids: list[Any]
    date_from: date
    date_to: date
    tz: ZoneInfo
    business_account_id: Any = None
    phone_number_id: Any = None
    template_id: Any = None
    campaign_id: Any = None
    language: str | None = None

    @property
    def bounds(self) -> tuple[datetime, datetime]:
        start, _ = day_bounds(self.date_from, self.tz)
        _, end = day_bounds(self.date_to, self.tz)
        return start, end

    @property
    def days(self) -> list[date]:
        return [
            self.date_from + timedelta(days=i)
            for i in range((self.date_to - self.date_from).days + 1)
        ]

    def filter(self, queryset: QuerySet[Any], *, dated: bool = True) -> QuerySet[Any]:
        qs = queryset.filter(business_id__in=self.business_ids)
        if dated:
            qs = qs.filter(date__gte=self.date_from, date__lte=self.date_to)
        if self.business_account_id:
            qs = qs.filter(business_account_id=self.business_account_id)
        if self.phone_number_id:
            qs = qs.filter(phone_number_id=self.phone_number_id)
        if self.template_id:
            qs = qs.filter(template_id=self.template_id)
        if self.campaign_id:
            qs = qs.filter(campaign_id=self.campaign_id)
        if self.language:
            qs = qs.filter(template__language=self.language)
        return qs


def _sums() -> dict[str, Any]:
    return {name: Sum(name) for name in COUNTER_FIELDS}


def _clean(row: dict[str, Any]) -> dict[str, int]:
    return {name: int(row.get(name) or 0) for name in COUNTER_FIELDS}


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def rates(counts: dict[str, int]) -> dict[str, float | None]:
    """Rates over messages whose outcome is known (in-flight excluded).
    ``None`` means "no data" — never a measured 0 %."""
    settled = counts["outbound"] - counts["in_flight"]
    return {
        "delivery_rate": _ratio(counts["delivered"], settled),
        "read_rate": _ratio(counts["read"], counts["delivered"]),
        "failure_rate": _ratio(counts["failed"], settled),
        "inbound_outbound_ratio": _ratio(counts["inbound"], counts["outbound"]),
    }


def _money(value: Decimal | None) -> str:
    return f"{(value or Decimal(0)).quantize(Decimal('0.000001'))}"


def cost_summary(scope: AnalyticsScope, counts: dict[str, int]) -> dict[str, Any]:
    rows = (
        scope.filter(CostMetricDaily.objects.all())
        .values("currency")
        .annotate(amount=Sum("amount"), messages=Sum("messages"))
        .order_by("currency")
    )
    by_currency = [
        {
            "currency": r["currency"],
            "amount": _money(r["amount"]),
            "priced_messages": int(r["messages"] or 0),
            "average_per_message": (
                _money((r["amount"] or Decimal(0)) / r["messages"]) if r["messages"] else None
            ),
        }
        for r in rows
    ]
    if by_currency:
        state = "priced" if not counts["cost_pending_rate"] else "partial"
    elif counts["cost_pending_rate"]:
        state = "pending_rate"
    else:
        state = "none"
    return {
        "state": state,
        "by_currency": by_currency,
        "pending_rate_messages": counts["cost_pending_rate"],
        "free_messages": counts["cost_free"],
    }


def freshness(scope: AnalyticsScope, computed_at: datetime | None) -> dict[str, Any]:
    return {
        "computed_at": computed_at.isoformat() if computed_at else None,
        "pending_buckets": AnalyticsDirtyBucket.objects.filter(
            business_id__in=scope.business_ids
        ).count(),
    }


def overview(scope: AnalyticsScope) -> dict[str, Any]:
    daily = scope.filter(MessageMetricDaily.objects.all())
    agg = daily.aggregate(**_sums(), rows=Count("id"), computed=Max("computed_at"))
    counts = _clean(agg)
    activity = scope.filter(ContactActivityDaily.objects.all())
    start, end = scope.bounds
    resolved = None
    if not (scope.template_id or scope.campaign_id or scope.language):
        conversations = Conversation.objects.filter(
            business_id__in=scope.business_ids,
            status=Conversation.Status.RESOLVED,
            status_changed_at__gte=start,
            status_changed_at__lt=end,
        )
        if scope.phone_number_id:
            conversations = conversations.filter(phone_number_id=scope.phone_number_id)
        if scope.business_account_id:
            conversations = conversations.filter(
                phone_number__business_account_id=scope.business_account_id
            )
        resolved = conversations.count()
    return {
        "has_data": bool(agg["rows"]),
        "totals": counts,
        "rates": rates(counts),
        "unique_contacts": activity.values("contact_id").distinct().count(),
        "active_conversations": activity.values("phone_number_id", "contact_id").distinct().count(),
        "resolved_conversations": resolved,
        "cost": cost_summary(scope, counts),
        "freshness": freshness(scope, agg["computed"]),
    }


def timeseries(scope: AnalyticsScope, granularity: str) -> dict[str, Any]:
    fields = ("inbound", "outbound", "sent", "delivered", "read", "failed", "in_flight")
    sums = {f: Sum(f) for f in fields}
    points: list[dict[str, Any]] = []
    if granularity == "hour":
        start, end = scope.bounds
        rows = {
            r["hour"]: r
            for r in scope.filter(MessageMetricHourly.objects.all(), dated=False)
            .filter(hour__gte=start, hour__lt=end)
            .values("hour")
            .annotate(**sums)
            .order_by("hour")
        }
        # Step in UTC (absolute hours) so DST transitions never skip/duplicate.
        cursor = start.astimezone(UTC)
        while cursor < end:
            row = rows.get(cursor, {})
            points.append(
                {"bucket": cursor.astimezone(scope.tz).isoformat()}
                | {f: int(row.get(f) or 0) for f in fields}
            )
            cursor += timedelta(hours=1)
        # Cost per hour is not tracked (costs roll up per day).
        return {"granularity": "hour", "points": points, "cost": []}

    rows_by_day = {
        r["date"]: r
        for r in scope.filter(MessageMetricDaily.objects.all())
        .values("date")
        .annotate(**sums)
        .order_by("date")
    }
    for day in scope.days:
        row = rows_by_day.get(day, {})
        points.append({"bucket": day.isoformat()} | {f: int(row.get(f) or 0) for f in fields})
    cost = [
        {"date": r["date"].isoformat(), "currency": r["currency"], "amount": _money(r["amount"])}
        for r in scope.filter(CostMetricDaily.objects.all())
        .values("date", "currency")
        .annotate(amount=Sum("amount"))
        .order_by("date", "currency")
    ]
    return {"granularity": "day", "points": points, "cost": cost}


def _costs_by(scope: AnalyticsScope, key: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in (
        scope.filter(CostMetricDaily.objects.all())
        .values(key, "currency")
        .annotate(amount=Sum("amount"), messages=Sum("messages"))
        .order_by(key, "currency")
    ):
        out[r[key]].append(
            {"currency": r["currency"], "amount": _money(r["amount"]), "messages": r["messages"]}
        )
    return out


def numbers(scope: AnalyticsScope) -> list[dict[str, Any]]:
    daily = scope.filter(MessageMetricDaily.objects.all())
    by_number = {
        r["phone_number_id"]: r
        for r in daily.values("phone_number_id")
        .annotate(**_sums(), templates_used=Count("template_id", distinct=True))
        .order_by()
    }
    activity = scope.filter(ContactActivityDaily.objects.all())
    contacts = {
        r["phone_number_id"]: r["n"]
        for r in activity.values("phone_number_id")
        .annotate(n=Count("contact_id", distinct=True))
        .order_by()
    }
    costs = _costs_by(scope, "phone_number_id")
    numbers_qs = WhatsAppPhoneNumber.objects.filter(business_id__in=scope.business_ids)
    if scope.business_account_id:
        numbers_qs = numbers_qs.filter(business_account_id=scope.business_account_id)
    if scope.phone_number_id:
        numbers_qs = numbers_qs.filter(pk=scope.phone_number_id)
    result = []
    for number in numbers_qs.select_related("business_account").order_by("display_phone_number"):
        row = by_number.get(number.pk)
        counts = _clean(row or {})
        result.append(
            {
                "phone_number_id": str(number.pk),
                "business_id": str(number.business_id),
                "display_phone_number": number.display_phone_number,
                "label": number.label,
                "verified_name": number.verified_name,
                "status": number.status,
                "quality_rating": number.quality_rating,
                "messaging_limit_tier": number.messaging_limit_tier,
                "business_account_external_id": number.business_account.external_id,
                "has_data": row is not None,
                "totals": counts,
                "rates": rates(counts),
                "unique_contacts": contacts.get(number.pk, 0),
                # A conversation is (number, contact), so per number the
                # active conversations equal the distinct active contacts.
                "active_conversations": contacts.get(number.pk, 0),
                "templates_used": int(row["templates_used"]) if row else 0,
                "cost": costs.get(number.pk, []),
            }
        )
    return result


def templates(scope: AnalyticsScope) -> list[dict[str, Any]]:
    daily = scope.filter(MessageMetricDaily.objects.filter(template__isnull=False))
    rows = list(
        daily.values("template_id")
        .annotate(**_sums(), first_date=Min("date"), last_date=Max("date"))
        .order_by("-templates_sent")
    )
    recipients = {
        r["template_id"]: r["n"]
        for r in scope.filter(
            ContactActivityDaily.objects.filter(template__isnull=False, outbound__gt=0)
        )
        .values("template_id")
        .annotate(n=Count("contact_id", distinct=True))
        .order_by()
    }
    costs = _costs_by(scope, "template_id")
    info = {
        t.pk: t
        for t in MessageTemplate.objects.filter(
            pk__in=[r["template_id"] for r in rows]
        ).select_related("business_account")
    }
    result = []
    for row in rows:
        template = info.get(row["template_id"])
        counts = _clean(row)
        result.append(
            {
                "template_id": str(row["template_id"]),
                "name": template.name if template else "",
                "language": template.language if template else "",
                "category": template.category if template else "",
                "status": template.status if template else "",
                "sends": counts["templates_sent"],
                "totals": counts,
                "rates": rates(counts),
                "unique_recipients": recipients.get(row["template_id"], 0),
                "first_used": row["first_date"].isoformat() if row["first_date"] else None,
                "last_used": row["last_date"].isoformat() if row["last_date"] else None,
                "cost": costs.get(row["template_id"], []),
            }
        )
    return result


COST_DIMENSIONS = {
    "business": "business_id",
    "business_account": "business_account_id",
    "phone_number": "phone_number_id",
    "template": "template_id",
    "campaign": "campaign_id",
    "category": "category",
    "date": "date",
}


def costs(scope: AnalyticsScope, group_by: str) -> dict[str, Any]:
    key = COST_DIMENSIONS[group_by]
    rows = list(
        scope.filter(CostMetricDaily.objects.all())
        .values(key, "currency")
        .annotate(amount=Sum("amount"), messages=Sum("messages"))
        .order_by(key, "currency")
    )
    labels = _labels(group_by, [r[key] for r in rows])
    return {
        "group_by": group_by,
        "rows": [
            {
                "key": str(r[key]) if r[key] is not None else None,
                "label": labels.get(r[key], "") if r[key] is not None else "(none)",
                "currency": r["currency"],
                "amount": _money(r["amount"]),
                "messages": int(r["messages"] or 0),
            }
            for r in rows
        ],
    }


def _labels(group_by: str, keys: list[Any]) -> dict[Any, str]:
    from apps.businesses.models import Business
    from apps.campaigns.models import Campaign
    from apps.whatsapp.models import WhatsAppBusinessAccount

    ids = [k for k in keys if k is not None]
    if group_by == "business":
        return {b.pk: b.name for b in Business.objects.filter(pk__in=ids)}
    if group_by == "business_account":
        return {
            a.pk: a.name or a.external_id
            for a in WhatsAppBusinessAccount.objects.filter(pk__in=ids)
        }
    if group_by == "phone_number":
        return {
            n.pk: n.display_phone_number for n in WhatsAppPhoneNumber.objects.filter(pk__in=ids)
        }
    if group_by == "template":
        return {
            t.pk: f"{t.name} ({t.language})" for t in MessageTemplate.objects.filter(pk__in=ids)
        }
    if group_by == "campaign":
        return {c.pk: c.name for c in Campaign.objects.filter(pk__in=ids)}
    return {k: str(k) for k in ids}


def failures(scope: AnalyticsScope, limit: int = 50) -> dict[str, Any]:
    """Failed outbound messages in range, grouped by cause (reads messages,
    but only the failed ones of the selected businesses and days)."""
    start, end = scope.bounds
    qs = Message.objects.filter(
        business_id__in=scope.business_ids,
        direction=Direction.OUTBOUND,
        status=MessageStatus.FAILED,
        occurred_at__gte=start,
        occurred_at__lt=end,
    )
    if scope.business_account_id:
        qs = qs.filter(business_account_id=scope.business_account_id)
    if scope.phone_number_id:
        qs = qs.filter(phone_number_id=scope.phone_number_id)
    if scope.template_id:
        qs = qs.filter(template_id=scope.template_id)
    if scope.campaign_id:
        qs = qs.filter(campaign_id=scope.campaign_id)
    groups: dict[str, dict[str, Any]] = {}
    for row in (
        qs.values("error_code", "error_details__provider_code")
        .annotate(n=Count("id"))
        .order_by("-n")[:500]
    ):
        category = categorize(
            row["error_code"], {"provider_code": row["error_details__provider_code"]}
        )
        group = groups.setdefault(
            category,
            {
                "category": category,
                "label": LABELS[category],
                "retryable": category in RETRYABLE,
                "count": 0,
                "codes": [],
            },
        )
        group["count"] += row["n"]
        group["codes"].append({"code": row["error_code"], "count": row["n"]})
    recent = [
        {
            "message_id": str(m.pk),
            "occurred_at": m.occurred_at.isoformat(),
            "phone_number": m.phone_number.display_phone_number,
            "contact": str(m.contact),
            "template": m.template.name if m.template else None,
            "campaign_id": str(m.campaign_id) if m.campaign_id else None,
            "error_code": m.error_code,
            "error_message": m.error_message,
            "category": categorize(m.error_code, m.error_details),
        }
        for m in qs.select_related("phone_number", "contact", "template").order_by("-occurred_at")[
            :limit
        ]
    ]
    return {
        "groups": sorted(groups.values(), key=lambda g: -g["count"]),
        "recent": recent,
        "total": sum(g["count"] for g in groups.values()),
    }
