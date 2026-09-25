from typing import Any

from celery import shared_task

from apps.campaigns import engine


@shared_task(acks_late=True)
def start_campaign_task(campaign_id: str) -> str:
    """Re-check + audience snapshot + first tick. Idempotent: no-op unless QUEUED."""
    return engine.start_campaign(campaign_id)


@shared_task(bind=True, acks_late=True, max_retries=3)
def campaign_tick_task(self: Any, campaign_id: str) -> str:
    """One rate-limited dispatch step; re-schedules itself while RUNNING.
    Safe to run twice (SKIP LOCKED + per-recipient idempotency keys)."""
    return engine.tick(campaign_id).status


@shared_task
def start_due_campaigns() -> int:
    return engine.start_due_campaigns()


@shared_task
def campaign_watchdog() -> int:
    return engine.watchdog()
