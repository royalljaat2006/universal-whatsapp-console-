from celery import shared_task

from apps.analytics.aggregation import process_dirty_buckets, recompute_recent


@shared_task
def aggregate_dirty_buckets() -> int:
    """Every minute: re-aggregate the (business, day) buckets whose source
    messages, statuses or costs changed."""
    return process_dirty_buckets()


@shared_task
def recompute_recent_days() -> int:
    """Hourly safety net for the last two local days of every business."""
    return recompute_recent(days=2)
