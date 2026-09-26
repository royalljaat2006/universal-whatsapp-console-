"""Celery queue topology.

Queues are split by *failure domain and latency profile*, not one per task:

- ``webhooks``  — inbound provider events. Latency-sensitive (realtime inbox);
                  must never be starved by bulk work.
- ``outbound``  — individual outbound sends (API/agent initiated).
- ``media``     — inbound media downloads (bandwidth-heavy, isolated so large
                  files never delay sends or webhook processing).
- ``campaigns`` — campaign fan-out and bulk sends. Isolated so a 100k-recipient
                  campaign cannot delay an agent's reply.
- ``sync``      — provider synchronisation (numbers, templates). Slow, rate-limited
                  by Meta per WABA.
- ``analytics`` — aggregation and cost calculation. Throughput-oriented.
- ``default``   — housekeeping (sweepers, notifications).

In local development one worker consumes every queue. In production run at
least: one worker for ``webhooks``, one for ``outbound,campaigns``, one for the
rest (see docs/deployment.md).
"""

from celery.schedules import crontab
from kombu import Queue

QUEUE_NAMES = ("webhooks", "outbound", "media", "campaigns", "sync", "analytics", "default")

TASK_QUEUES = tuple(Queue(name) for name in QUEUE_NAMES)

TASK_ROUTES = {
    "apps.webhooks.tasks.*": {"queue": "webhooks"},
    "apps.messaging.tasks.send_outbound_message": {"queue": "outbound"},
    "apps.messaging.tasks.download_inbound_media": {"queue": "media"},
    "apps.messaging.tasks.*": {"queue": "default"},
    "apps.campaigns.tasks.*": {"queue": "campaigns"},
    "apps.whatsapp.tasks.*": {"queue": "sync"},
    "apps.templates.tasks.*": {"queue": "sync"},
    "apps.analytics.tasks.*": {"queue": "analytics"},
    "apps.reports.tasks.*": {"queue": "analytics"},
    "apps.contacts.tasks.*": {"queue": "analytics"},
    "apps.billing.tasks.*": {"queue": "analytics"},
    "apps.notifications.tasks.*": {"queue": "default"},
    "apps.identity.tasks.*": {"queue": "default"},
}

BEAT_SCHEDULE = {
    # Safety net for the webhook outbox: events persisted while the broker
    # was unavailable (or whose task was lost) are re-enqueued.
    "webhooks-requeue-stale": {
        "task": "apps.webhooks.tasks.requeue_stale_webhook_events",
        "schedule": 60.0,
    },
    # Re-dispatches QUEUED messages whose enqueue was lost (broker outage).
    "messaging-sweep-stale-queued": {
        "task": "apps.messaging.tasks.sweep_stale_queued_messages",
        "schedule": crontab(minute="*/2"),
    },
    # Re-enqueues inbound media downloads whose task was lost.
    "messaging-sweep-pending-media": {
        "task": "apps.messaging.tasks.sweep_pending_media_downloads",
        "schedule": crontab(minute="*/10"),
    },
    # Prices billable messages once a matching rate card is loaded.
    "billing-price-pending": {
        "task": "apps.billing.tasks.price_pending_message_costs",
        "schedule": crontab(minute=15),
    },
    # Campaigns: start due scheduled campaigns; re-kick stalled executions.
    "campaigns-start-due": {
        "task": "apps.campaigns.tasks.start_due_campaigns",
        "schedule": 60.0,
    },
    "campaigns-watchdog": {
        "task": "apps.campaigns.tasks.campaign_watchdog",
        "schedule": 60.0,
    },
    # Analytics: re-aggregate changed buckets; hourly safety net for recent days.
    "analytics-aggregate-dirty": {
        "task": "apps.analytics.tasks.aggregate_dirty_buckets",
        "schedule": 60.0,
    },
    "analytics-recompute-recent": {
        "task": "apps.analytics.tasks.recompute_recent_days",
        "schedule": crontab(minute=40),
    },
    # Fails (never re-sends) messages stuck in SENDING after a worker crash.
    "messaging-sweep-stuck-sending": {
        "task": "apps.messaging.tasks.sweep_stuck_sending_messages",
        "schedule": crontab(minute="*/5"),
    },
}
