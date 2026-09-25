"""Rebuild analytics rollups from the source of truth.

``recompute_business_day`` is the only writer of the derived tables. It
reads one business-local day of messages (index ``msg_business_occurred_idx``)
and replaces that day's rows inside one transaction, serialised per
(business, day) by a Postgres advisory lock. Running it twice gives the same
result, so late webhooks, replays and rebuilds are always safe.
"""

import logging
import zlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db import connection, transaction
from django.db.models import Count, Q, QuerySet, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone

from apps.analytics.models import (
    AnalyticsDirtyBucket,
    ContactActivityDaily,
    CostMetricDaily,
    MessageMetricDaily,
    MessageMetricHourly,
)
from apps.analytics.tracking import business_zone, day_bounds, local_dates_for_utc_date
from apps.billing.models import MessageCost
from apps.businesses.models import Business
from apps.messaging.models import Direction, Message
from apps.messaging.state import MessageStatus

logger = logging.getLogger(__name__)

MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")
OUT = Q(direction=Direction.OUTBOUND)
IN = Q(direction=Direction.INBOUND)

_REACHED_SENT = [MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.READ]
_REACHED_ACCEPTED = [MessageStatus.ACCEPTED, *_REACHED_SENT]
_REACHED_DELIVERED = [MessageStatus.DELIVERED, MessageStatus.READ]

# Counter name -> filter over messages. A message counts as e.g. "delivered"
# when WhatsApp reported it delivered *or* read (read implies delivery), using
# both the status and the per-status timestamp so out-of-order webhooks count.
COUNTERS: dict[str, Q] = {
    "inbound": IN,
    "outbound": OUT,
    "in_flight": OUT & Q(status__in=[MessageStatus.QUEUED, MessageStatus.SENDING]),
    "accepted": OUT & (Q(accepted_at__isnull=False) | Q(status__in=_REACHED_ACCEPTED)),
    "sent": OUT & (Q(sent_at__isnull=False) | Q(status__in=_REACHED_SENT)),
    "delivered": OUT & (Q(delivered_at__isnull=False) | Q(status__in=_REACHED_DELIVERED)),
    "read": OUT & (Q(read_at__isnull=False) | Q(status=MessageStatus.READ)),
    "failed": OUT & Q(status=MessageStatus.FAILED),
    "rejected": OUT
    & Q(status=MessageStatus.FAILED, accepted_at__isnull=True, external_id__isnull=True),
    "templates_sent": OUT & Q(template__isnull=False),
    "media_sent": OUT & Q(message_type__in=MEDIA_TYPES),
    "media_received": IN & Q(message_type__in=MEDIA_TYPES),
    "cost_priced": OUT & Q(cost_status=Message.CostStatus.PRICED),
    "cost_pending_rate": OUT & Q(cost_status=Message.CostStatus.PENDING_RATE),
    "cost_free": OUT & Q(cost_status=Message.CostStatus.FREE),
}
COUNTER_FIELDS = tuple(COUNTERS)

_DIMS = ("phone_number_id", "phone_number__business_account_id", "template_id", "campaign_id")


def _counter_annotations() -> dict[str, Any]:
    return {name: Count("id", filter=condition) for name, condition in COUNTERS.items()}


def _dims(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "phone_number_id": row["phone_number_id"],
        "business_account_id": row["phone_number__business_account_id"],
        "template_id": row["template_id"],
        "campaign_id": row["campaign_id"],
    }


def _lock(business_id: Any, day: date) -> None:
    key = zlib.crc32(f"analytics:{business_id}".encode()) & 0x7FFFFFFF
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [key, day.toordinal()])


@dataclass(frozen=True)
class RecomputeResult:
    business_id: Any
    date: date
    daily_rows: int
    hourly_rows: int
    cost_rows: int
    activity_rows: int


def _messages(business: Business, day: date) -> QuerySet[Message]:
    start, end = day_bounds(day, business_zone(business))
    return Message.objects.filter(business=business, occurred_at__gte=start, occurred_at__lt=end)


def recompute_business_day(business: Business, day: date) -> RecomputeResult:
    tz = business_zone(business)
    now = timezone.now()
    base = {"organization_id": business.organization_id, "business_id": business.pk}
    with transaction.atomic():
        _lock(business.pk, day)
        messages = _messages(business, day)

        daily = [
            MessageMetricDaily(
                **base,
                **_dims(row),
                date=day,
                computed_at=now,
                **{name: row[name] for name in COUNTER_FIELDS},
            )
            for row in messages.values(*_DIMS).annotate(**_counter_annotations()).order_by()
        ]
        hourly = [
            MessageMetricHourly(
                **base,
                **_dims(row),
                hour=row["bucket"],
                date=day,
                computed_at=now,
                **{name: row[name] for name in COUNTER_FIELDS},
            )
            for row in messages.annotate(bucket=TruncHour("occurred_at", tzinfo=tz))
            .values("bucket", *_DIMS)
            .annotate(**_counter_annotations())
            .order_by()
        ]
        costs = [
            CostMetricDaily(
                **base,
                phone_number_id=row["message__phone_number_id"],
                business_account_id=row["message__phone_number__business_account_id"],
                template_id=row["message__template_id"],
                campaign_id=row["message__campaign_id"],
                date=day,
                category=row["category"],
                currency=row["currency"],
                amount=row["amount"] or 0,
                messages=row["count"],
                computed_at=now,
            )
            for row in MessageCost.objects.filter(message__in=messages)
            .values(*(f"message__{d}" for d in _DIMS), "category", "currency")
            .annotate(amount=Sum("amount"), count=Count("id"))
            .order_by()
        ]
        activity = [
            ContactActivityDaily(
                **base,
                phone_number_id=row["phone_number_id"],
                business_account_id=row["phone_number__business_account_id"],
                contact_id=row["contact_id"],
                template_id=row["template_id"],
                campaign_id=row["campaign_id"],
                date=day,
                inbound=row["inbound"],
                outbound=row["outbound"],
            )
            for row in messages.values(*_DIMS, "contact_id")
            .annotate(inbound=Count("id", filter=IN), outbound=Count("id", filter=OUT))
            .order_by()
        ]

        for model in (
            MessageMetricDaily,
            MessageMetricHourly,
            CostMetricDaily,
            ContactActivityDaily,
        ):
            model.objects.filter(business=business, date=day).delete()
        MessageMetricDaily.objects.bulk_create(daily, batch_size=1000)
        MessageMetricHourly.objects.bulk_create(hourly, batch_size=1000)
        CostMetricDaily.objects.bulk_create(costs, batch_size=1000)
        ContactActivityDaily.objects.bulk_create(activity, batch_size=1000)
    return RecomputeResult(business.pk, day, len(daily), len(hourly), len(costs), len(activity))


def process_dirty_buckets(limit: int = 200) -> int:
    """Recompute buckets marked dirty by the messaging hot path."""
    processed = 0
    for bucket in list(AnalyticsDirtyBucket.objects.order_by("marked_at")[:limit]):
        # Delete first: a change racing with this recompute re-marks the bucket.
        deleted, _ = AnalyticsDirtyBucket.objects.filter(pk=bucket.pk).delete()
        if not deleted:
            continue  # another worker took it
        business = (
            Business.objects.select_related("organization").filter(pk=bucket.business_id).first()
        )
        if business is None:
            continue
        try:
            for day in local_dates_for_utc_date(bucket.utc_date, business_zone(business)):
                recompute_business_day(business, day)
        except Exception:
            # Keep the bucket dirty so the next run retries it; never lose it.
            AnalyticsDirtyBucket.objects.bulk_create(
                [AnalyticsDirtyBucket(business_id=bucket.business_id, utc_date=bucket.utc_date)],
                ignore_conflicts=True,
            )
            logger.exception(
                "analytics recompute failed",
                extra={"business_id": str(business.pk), "utc_date": bucket.utc_date.isoformat()},
            )
            continue
        processed += 1
    return processed


def recompute_recent(days: int = 2) -> int:
    """Safety net: recompute the most recent local days of every business, in
    case a dirty mark was lost (e.g. a failed transaction after marking)."""
    count = 0
    for business in Business.objects.select_related("organization"):
        today = timezone.now().astimezone(business_zone(business)).date()
        for offset in range(days):
            recompute_business_day(business, today - timedelta(days=offset))
            count += 1
    return count


def rebuild(business: Business, date_from: date, date_to: date) -> int:
    count = 0
    day = date_from
    while day <= date_to:
        recompute_business_day(business, day)
        day += timedelta(days=1)
        count += 1
    return count
