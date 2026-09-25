"""Dirty-bucket tracking and business-local time helpers."""

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.analytics.models import AnalyticsDirtyBucket

logger = logging.getLogger(__name__)


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def business_zone(business: Any) -> ZoneInfo:
    """Business.timezone, else its organization's, else UTC."""
    name = getattr(business, "timezone", "") or getattr(
        getattr(business, "organization", None), "timezone", ""
    )
    return zone(name)


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) of a local calendar day (DST-safe)."""
    return (
        datetime.combine(day, time.min, tzinfo=tz),
        datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz),
    )


def local_dates_for_utc_date(utc_date: date, tz: ZoneInfo) -> list[date]:
    start = datetime.combine(utc_date, time.min, tzinfo=UTC)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return sorted({start.astimezone(tz).date(), end.astimezone(tz).date()})


def mark(business_id: Any, at: datetime) -> None:
    AnalyticsDirtyBucket.objects.bulk_create(
        [AnalyticsDirtyBucket(business_id=business_id, utc_date=at.astimezone(UTC).date())],
        ignore_conflicts=True,
    )


def mark_message(message: Any) -> None:
    if not message.business_id or not message.occurred_at:
        return
    mark(message.business_id, message.occurred_at)
