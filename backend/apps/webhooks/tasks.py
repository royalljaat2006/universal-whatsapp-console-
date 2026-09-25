from celery import shared_task

from apps.webhooks.processor import due_event_ids, process_event


@shared_task(acks_late=True)
def process_webhook_event(event_id: str, trigger: str = "queue") -> str:
    return process_event(event_id, trigger=trigger).status


@shared_task
def requeue_stale_webhook_events() -> int:
    """Outbox sweeper: (re)enqueue events that are pending or due for retry."""
    ids = due_event_ids()
    for event_id in ids:
        process_webhook_event.delay(str(event_id), trigger="sweeper")
    return len(ids)
