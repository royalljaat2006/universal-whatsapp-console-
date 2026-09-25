from typing import Any

from celery import shared_task

from apps.messaging.services.media_download import (
    download_inbound_attachment,
    requeue_pending_downloads,
)
from apps.messaging.services.outbound import (
    dispatch_outbound_message,
    fail_stuck_sending_messages,
    requeue_stale_queued_messages,
)


@shared_task(bind=True, max_retries=None, acks_late=True)
def send_outbound_message(self: Any, message_id: str) -> str:
    """Idempotent: dispatch is a no-op unless the message is QUEUED. Throttle
    waits and safe retries re-schedule this same task."""
    outcome = dispatch_outbound_message(message_id)  # type: ignore[arg-type]
    if outcome.retry_in is not None:
        raise self.retry(countdown=outcome.retry_in)
    return outcome.status


@shared_task
def sweep_stuck_sending_messages() -> int:
    return fail_stuck_sending_messages()


@shared_task
def sweep_stale_queued_messages() -> int:
    return requeue_stale_queued_messages()


@shared_task(bind=True, max_retries=None, acks_late=True)
def download_inbound_media(self: Any, attachment_id: str) -> str:
    """Idempotent: a stored attachment is never downloaded again."""
    outcome = download_inbound_attachment(attachment_id)
    if outcome.retry_in is not None:
        raise self.retry(countdown=outcome.retry_in)
    return outcome.status


@shared_task
def sweep_pending_media_downloads() -> int:
    return requeue_pending_downloads()
