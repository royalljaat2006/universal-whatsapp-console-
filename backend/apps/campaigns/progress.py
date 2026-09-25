"""Campaign progress derived from real recipient/message state.

``sync_recipient_from_message`` runs inside the transaction that changed a
campaign message (via apps.messaging.services.hooks), so a recipient's status
is always exactly its latest message's status — counters are never
incremented ahead of the underlying event.
"""

from typing import Any

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignRecipient
from apps.messaging.models import Message
from apps.messaging.services.failure_categories import categorize
from apps.messaging.state import MessageStatus
from apps.realtime import publisher

EVENT_CAMPAIGN_UPDATED = "campaign.updated"
EVENT_CAMPAIGN_PROGRESS = "campaign.progress"
CANCELLED_CODE = "CAMPAIGN_CANCELLED"
PROGRESS_COALESCE_SECONDS = 2

R = CampaignRecipient.Status
MESSAGE_TO_RECIPIENT: dict[str, str] = {
    MessageStatus.QUEUED: R.QUEUED,
    MessageStatus.SENDING: R.SENDING,
    MessageStatus.ACCEPTED: R.ACCEPTED,
    MessageStatus.SENT: R.SENT,
    MessageStatus.DELIVERED: R.DELIVERED,
    MessageStatus.READ: R.READ,
    MessageStatus.FAILED: R.FAILED,
}
IN_FLIGHT = (R.QUEUED, R.SENDING)
NOT_DONE = (R.PENDING, R.QUEUED, R.SENDING)


def recipient_status_for(message: Message) -> str:
    if message.status == MessageStatus.FAILED and message.error_code == CANCELLED_CODE:
        return R.CANCELLED
    return MESSAGE_TO_RECIPIENT.get(message.status, R.QUEUED)


def sync_recipient_from_message(message: Message) -> None:
    if message.campaign_id is None:
        return
    status = recipient_status_for(message)
    changes: dict[str, Any] = {
        "message": message,
        "status": status,
        "status_changed_at": timezone.now(),
    }
    if status == R.FAILED:
        changes.update(
            error_code=message.error_code[:64],
            error_message=message.error_message[:500],
            failure_category=categorize(message.error_code, message.error_details),
        )
    # The idempotency key "campaign:<campaign>:<recipient>:<attempt>" names
    # exactly one recipient attempt, so a late webhook for an earlier attempt
    # (after an operator retry) can never overwrite the current attempt.
    target = _recipient_attempt(message)
    if target is None:
        query = Q(message_id=message.pk)
    else:
        recipient_id, attempt = target
        query = Q(pk=recipient_id, attempts=attempt)
    updated = (
        CampaignRecipient.objects.filter(query, campaign_id=message.campaign_id)
        .exclude(status__in=[R.SKIPPED, R.CANCELLED])
        .update(**changes)
    )
    if updated:
        publish_progress(message.campaign_id, business_id=message.business_id)


def _recipient_attempt(message: Message) -> tuple[str, int] | None:
    parts = (message.idempotency_key or "").split(":")
    if len(parts) != 4 or parts[0] != "campaign" or parts[1] != str(message.campaign_id):
        return None
    try:
        return parts[2], int(parts[3])
    except ValueError:
        return None


def summarize(by_status: dict[str, int]) -> dict[str, Any]:
    per_status = {s: by_status.get(s, 0) for s in R.values}
    total = sum(per_status.values())
    not_done = sum(per_status[s] for s in NOT_DONE)
    # "Reached at least" counters for reporting (read implies delivered …).
    reached_sent = per_status[R.SENT] + per_status[R.DELIVERED] + per_status[R.READ]
    return {
        "total": total,
        "by_status": per_status,
        "processed": total - not_done,
        "in_flight": per_status[R.QUEUED] + per_status[R.SENDING],
        "accepted_or_later": per_status[R.ACCEPTED] + reached_sent,
        "sent_or_later": reached_sent,
        "delivered_or_later": per_status[R.DELIVERED] + per_status[R.READ],
        "read": per_status[R.READ],
        "failed": per_status[R.FAILED],
        "skipped": per_status[R.SKIPPED],
        "cancelled": per_status[R.CANCELLED],
        "pending": per_status[R.PENDING],
    }


def bulk_counts(campaign_ids: list[Any]) -> dict[Any, dict[str, Any]]:
    """Counters for many campaigns in ONE grouped query (no N+1)."""
    rows: dict[Any, dict[str, int]] = {campaign_id: {} for campaign_id in campaign_ids}
    for row in (
        CampaignRecipient.objects.filter(campaign_id__in=campaign_ids)
        .values("campaign_id", "status")
        .annotate(n=Count("id"))
        .order_by()
    ):
        rows[row["campaign_id"]][row["status"]] = row["n"]
    return {campaign_id: summarize(by_status) for campaign_id, by_status in rows.items()}


def counts(campaign_id: Any) -> dict[str, Any]:
    return bulk_counts([campaign_id])[campaign_id]


def _payload(campaign: Campaign) -> dict[str, Any]:
    return {
        "id": str(campaign.pk),
        "business_id": str(campaign.business_id),
        "status": campaign.status,
        "progress": counts(campaign.pk),
    }


def publish_updated(campaign: Campaign) -> None:
    publisher.publish_on_commit(campaign.business_id, EVENT_CAMPAIGN_UPDATED, _payload(campaign))


def publish_progress(campaign_id: Any, *, business_id: Any = None, force: bool = False) -> None:
    """At most one progress event per campaign every couple of seconds; the
    execution tick publishes unconditionally, so the last state is always
    sent while the campaign runs (clients also re-read over REST)."""
    if not force and not cache.add(
        f"campaign-progress:{campaign_id}", 1, PROGRESS_COALESCE_SECONDS
    ):
        return
    campaign = Campaign.objects.filter(pk=campaign_id).only("pk", "business_id", "status").first()
    if campaign is None:
        return
    publisher.publish_on_commit(
        business_id or campaign.business_id, EVENT_CAMPAIGN_PROGRESS, _payload(campaign)
    )
