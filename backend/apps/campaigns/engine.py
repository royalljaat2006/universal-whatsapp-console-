"""Campaign execution: launch → snapshot → rate-limited ticks → completion.

Flow (never inside an HTTP request)::

    launch (API)            validate → SCHEDULED | QUEUED (+ start task on commit)
    start_due_campaigns     beat, every minute: due SCHEDULED → QUEUED → start
    start_campaign          re-check → audience snapshot (CampaignRecipient rows,
                            variables resolved) → RUNNING → first tick
    tick                    claim ≤ rate·tick/60 PENDING recipients (SKIP LOCKED)
                            → enqueue_outbound_message (idempotency key per
                            recipient attempt) → next tick … → COMPLETED
    watchdog                beat: RUNNING campaigns whose tick chain died

Idempotency: each recipient attempt has the key
``campaign:<campaign>:<recipient>:<attempt>`` and messages are unique on
(organization, idempotency_key). A Celery retry, a duplicated tick, a worker
crash between "claimed" and "linked", or a replayed task therefore re-uses the
same message instead of sending again. The attempt number only changes when an
operator explicitly retries *safe* failures.

Rate limiting: the campaign rate caps how many recipients a tick hands to the
messaging pipeline, an in-flight window keeps pause/cancel responsive, and the
per-number token bucket plus provider-error backoff still apply at dispatch.
"""

import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.campaigns import audience as audience_mod
from apps.campaigns import checks, progress, variables
from apps.campaigns.models import Campaign, CampaignEvent, CampaignRecipient
from apps.contacts.models import ContactIdentity
from apps.notifications import services as notifications
from apps.messaging.services.errors import (
    PhoneNumberNotReadyError,
    TemplateNotSendableError,
    TemplateParametersInvalidError,
)
from apps.messaging.services.failure_categories import RETRYABLE, categorize
from apps.messaging.services.outbound import (
    SendRequest,
    SendScope,
    TemplateSend,
    cancel_queued_message,
    enqueue_outbound_message,
)
from core.exceptions import ConfigurationRequiredError, ConflictError, DomainError

logger = logging.getLogger("campaigns.engine")

S = Campaign.Status
R = CampaignRecipient.Status
SNAPSHOT_BATCH = 1000
ORPHAN_AFTER = timedelta(seconds=60)


class CampaignStateError(ConflictError):
    code = "CAMPAIGN_STATE_CONFLICT"
    default_message = "This action is not allowed in the campaign's current state."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def record_event(campaign: Campaign, event: str, actor: Any = None, **data: Any) -> None:
    CampaignEvent.objects.create(
        organization_id=campaign.organization_id,
        campaign=campaign,
        event=event,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        data=data,
    )


def _locked(campaign_id: Any) -> Campaign:
    campaign = (
        Campaign.objects.select_for_update(of=("self",))
        .select_related(
            "organization", "business__organization", "phone_number__business_account", "template"
        )
        .filter(pk=campaign_id)
        .first()
    )
    if campaign is None:
        raise DomainError("campaign not found")
    return campaign


def _require(campaign: Campaign, allowed: set[str], action: str) -> None:
    if campaign.status not in allowed:
        raise CampaignStateError(
            f"Cannot {action} a campaign that is {campaign.status}.",
            details={"status": campaign.status, "allowed": sorted(allowed)},
        )


def _audit(action: str, campaign: Campaign, actor: Any, request: Any = None, **meta: Any) -> None:
    if actor is None:
        return
    audit(
        action,
        organization=campaign.organization,
        actor=actor,
        resource=campaign,
        metadata={"business_id": str(campaign.business_id), **meta},
        request=request,
    )


def _schedule_start(campaign_id: Any) -> None:
    from apps.campaigns.tasks import start_campaign_task

    transaction.on_commit(lambda: _safe_delay(start_campaign_task, str(campaign_id)))


def _schedule_tick(campaign_id: Any, countdown: float = 0) -> None:
    if not settings.CAMPAIGN_AUTO_TICK:
        return
    from apps.campaigns.tasks import campaign_tick_task

    def send() -> None:
        try:
            campaign_tick_task.apply_async((str(campaign_id),), countdown=countdown)
        except Exception:  # broker down: the watchdog re-kicks the campaign
            logger.exception(
                "could not schedule campaign tick", extra={"campaign_id": str(campaign_id)}
            )

    transaction.on_commit(send)


def _safe_delay(task: Any, *args: Any) -> None:
    try:
        task.delay(*args)
    except Exception:
        logger.exception("could not enqueue campaign task", extra={"task": task.name})


def _notify_campaign_finished(campaign: Campaign, kind: str, title: str) -> None:
    if campaign.launched_by_id is None:
        return
    from apps.organizations.services import get_active_membership

    membership = get_active_membership(campaign.launched_by, campaign.organization_id)
    if membership is not None:
        notifications.notify(
            organization=campaign.organization,
            recipient=membership,
            kind=kind,
            title=title,
            body=campaign.name,
            data={"campaign_id": str(campaign.pk)},
        )


def _fail_campaign(campaign: Campaign, code: str, reason: str) -> None:
    campaign.status = S.FAILED
    campaign.failure_code = code[:64]
    campaign.failure_reason = reason[:500]
    campaign.save(update_fields=["status", "failure_code", "failure_reason", "updated_at"])
    record_event(campaign, "failed", code=code, reason=reason[:500])
    progress.publish_updated(campaign)
    _notify_campaign_finished(campaign, notifications.KIND_CAMPAIGN_FAILED, "Campaign failed")
    logger.warning("campaign failed", extra={"campaign_id": str(campaign.pk), "code": code})


# ---------------------------------------------------------------------------
# Launch / schedule
# ---------------------------------------------------------------------------


def launch(
    campaign_id: Any, *, actor: Any, request: Any = None
) -> tuple[Campaign, checks.LaunchReport]:
    with transaction.atomic():
        campaign = _locked(campaign_id)
        _require(campaign, {S.DRAFT}, "launch")
        report = checks.evaluate(campaign)
        if not report.launchable:
            raise checks.CampaignNotLaunchableError(details=report.as_dict())
        now = timezone.now()
        campaign.launched_at = now
        campaign.launched_by = actor if getattr(actor, "is_authenticated", False) else None
        campaign.failure_code = campaign.failure_reason = ""
        scheduled = campaign.scheduled_at is not None and campaign.scheduled_at > now
        campaign.status = S.SCHEDULED if scheduled else S.QUEUED
        campaign.save(
            update_fields=[
                "status",
                "launched_at",
                "launched_by",
                "failure_code",
                "failure_reason",
                "updated_at",
            ]
        )
        summary = report.summary
        # Publish the state change before scheduling work: on-commit callbacks
        # run in registration order, so clients see QUEUED before RUNNING.
        progress.publish_updated(campaign)
        if scheduled:
            record_event(campaign, "scheduled", actor, scheduled_at=campaign.scheduled_at.isoformat())  # type: ignore[union-attr]
            _audit(actions.CAMPAIGN_SCHEDULED, campaign, actor, request, summary=summary)
        else:
            record_event(campaign, "launched", actor, recipients=summary.get("recipients"))
            _audit(actions.CAMPAIGN_LAUNCHED, campaign, actor, request, summary=summary)
            _schedule_start(campaign.pk)
    return campaign, report


def start_due_campaigns(limit: int = 50) -> int:
    now = timezone.now()
    due = list(
        Campaign.objects.filter(status=S.SCHEDULED, scheduled_at__lte=now)
        .order_by("scheduled_at")
        .values_list("pk", flat=True)[:limit]
    )
    started = 0
    for campaign_id in due:
        with transaction.atomic():
            moved = Campaign.objects.filter(pk=campaign_id, status=S.SCHEDULED).update(
                status=S.QUEUED, updated_at=now
            )
            if moved:
                campaign = Campaign.objects.get(pk=campaign_id)
                record_event(campaign, "launched", reason="schedule reached")
                progress.publish_updated(campaign)
                _schedule_start(campaign_id)
                started += 1
    return started


# ---------------------------------------------------------------------------
# Start: re-check + audience snapshot
# ---------------------------------------------------------------------------


def build_snapshot(campaign: Campaign) -> dict[str, int]:
    """Materialise the audience once. Idempotent (unique campaign+contact);
    later contact edits do not change the recipient list."""
    business = campaign.business
    definition = audience_mod.validate_definition(business, campaign.audience_definition)
    mapping = variables.validate_mapping(campaign.template, business, campaign.variable_mapping)
    bsuid = ContactIdentity.objects.filter(
        contact_id=OuterRef("pk"), kind=ContactIdentity.Kind.BSUID
    ).values("value")[:1]
    contacts = (
        audience_mod.audience_queryset(business, definition)
        .annotate(bsuid=Subquery(bsuid))
        .order_by("pk")
        .values(*variables.CONTACT_FIELDS, "bsuid")
    )
    now = timezone.now()
    batch: list[CampaignRecipient] = []
    skipped = 0

    def flush() -> None:
        CampaignRecipient.objects.bulk_create(batch, ignore_conflicts=True)
        batch.clear()

    for contact in contacts.iterator(chunk_size=2000):
        status, skip_reason, error, params = R.PENDING, "", "", {}
        try:
            params = variables.resolve(campaign.template, mapping, contact).parameters
        except variables.VariableMissingError as exc:
            status, skip_reason, error = R.SKIPPED, "VARIABLE_MISSING", f"No value for {exc.path}"
        except variables.TemplateParameterError as exc:
            status, skip_reason = R.SKIPPED, "VARIABLE_INVALID"
            error = "; ".join(f"{k}: {v}" for k, v in exc.errors.items())[:500]
        skipped += status == R.SKIPPED
        batch.append(
            CampaignRecipient(
                organization_id=campaign.organization_id,
                campaign=campaign,
                contact_id=contact["pk"],
                phone_number=contact["phone_number"] or "",
                bsuid=contact["bsuid"] or "",
                variables=params,
                status=status,
                status_changed_at=now,
                attempts=1,
                skip_reason=skip_reason,
                error_message=error,
            )
        )
        if len(batch) >= SNAPSHOT_BATCH:
            flush()
    if batch:
        flush()
    total = CampaignRecipient.objects.filter(campaign=campaign).count()
    campaign.audience_count = total
    campaign.snapshot_built_at = now
    return {"recipients": total, "skipped": skipped}


def start_campaign(campaign_id: Any) -> str:
    with transaction.atomic():
        campaign = _locked(campaign_id)
        if campaign.status != S.QUEUED:
            return campaign.status
        report = checks.evaluate(campaign, for_start=True)
        if not report.launchable:
            failed = "; ".join(
                c["message"] for c in report.checks if not c["ok"] and c["severity"] == "error"
            )
            _fail_campaign(campaign, "PRECHECK_FAILED", failed)
            return campaign.status
        if campaign.snapshot_built_at is None:
            stats = build_snapshot(campaign)
            record_event(campaign, "snapshot_built", **stats)
        now = timezone.now()
        campaign.status = S.RUNNING
        campaign.started_at = campaign.started_at or now
        campaign.last_tick_at = now
        campaign.save(
            update_fields=[
                "status",
                "started_at",
                "last_tick_at",
                "audience_count",
                "snapshot_built_at",
                "updated_at",
            ]
        )
        record_event(campaign, "started", recipients=campaign.audience_count)
        progress.publish_updated(campaign)
        _schedule_tick(campaign.pk)
    return campaign.status


# ---------------------------------------------------------------------------
# Tick: rate-limited dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickResult:
    status: str
    dispatched: int = 0
    next_in: float | None = None


def tick_budget(rate_per_minute: int, tick_seconds: int) -> int:
    return max(1, math.ceil(rate_per_minute * tick_seconds / 60))


def _claim(campaign: Campaign, allowed: int) -> list[CampaignRecipient]:
    now = timezone.now()
    # Claimed by an earlier tick that crashed before handing them over. Their
    # idempotency key is unchanged, so re-sending returns the same message if
    # one had in fact been created.
    orphans = list(
        CampaignRecipient.objects.select_for_update(skip_locked=True).filter(
            campaign=campaign,
            status=R.QUEUED,
            message__isnull=True,
            status_changed_at__lt=now - ORPHAN_AFTER,
        )[:allowed]
    )
    remaining = max(0, allowed - len(orphans))
    fresh = list(
        CampaignRecipient.objects.select_for_update(skip_locked=True)
        .filter(campaign=campaign, status=R.PENDING)
        .order_by("pk")[:remaining]
    )
    if fresh:
        CampaignRecipient.objects.filter(pk__in=[r.pk for r in fresh]).update(
            status=R.QUEUED, status_changed_at=now
        )
    return orphans + fresh


def _send_one(campaign: Campaign, recipient: CampaignRecipient) -> str | None:
    """Hand one recipient to the messaging pipeline. Returns a campaign-level
    failure code when the whole campaign must stop, else None."""
    request = SendRequest(
        content=TemplateSend(template_id=campaign.template_id, parameters=recipient.variables),
        phone_number_id=campaign.phone_number_id,
        recipient_phone=recipient.phone_number or None,
        recipient_bsuid=None if recipient.phone_number else (recipient.bsuid or None),
        idempotency_key=recipient.idempotency_key(),
    )
    try:
        with transaction.atomic():
            message, _ = enqueue_outbound_message(
                organization=campaign.organization,
                request=request,
                scope=SendScope(business_id=campaign.business_id),
                campaign=campaign,
                dispatch_queue="campaigns",
            )
            # Link in the same transaction as the message insert (dispatch is
            # only enqueued on commit, so the mirror is never overtaken). Also
            # covers idempotent replays, which do not re-run message hooks.
            progress.sync_recipient_from_message(message)
        return None
    except (PhoneNumberNotReadyError, TemplateNotSendableError, ConfigurationRequiredError) as exc:
        CampaignRecipient.objects.filter(pk=recipient.pk).update(status=R.PENDING)
        return f"{exc.code}: {exc.message}"
    except DomainError as exc:
        code = exc.code
        if isinstance(exc, TemplateParametersInvalidError):
            code = "TEMPLATE_PARAMETERS_INVALID"
        CampaignRecipient.objects.filter(pk=recipient.pk).update(
            status=R.FAILED,
            status_changed_at=timezone.now(),
            error_code=code[:64],
            error_message=exc.message[:500],
            failure_category=categorize(code),
        )
        return None


def tick(campaign_id: Any) -> TickResult:
    tick_seconds = settings.CAMPAIGN_TICK_SECONDS
    with transaction.atomic():
        campaign = (
            Campaign.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("organization", "business", "phone_number", "template")
            .filter(pk=campaign_id)
            .first()
        )
        if campaign is None:
            return TickResult("locked")  # another tick holds it (or it is gone)
        if campaign.status != S.RUNNING:
            return TickResult(campaign.status)
        campaign.last_tick_at = timezone.now()
        campaign.save(update_fields=["last_tick_at", "updated_at"])
        budget = tick_budget(campaign.max_messages_per_minute, tick_seconds)
        in_flight = CampaignRecipient.objects.filter(
            campaign=campaign, status__in=progress.IN_FLIGHT
        ).count()
        window = max(budget * 2, 10)
        claimed = _claim(campaign, max(0, min(budget, window - in_flight)))

    dispatched = 0
    for index, recipient in enumerate(claimed):
        failure = _send_one(campaign, recipient)
        if failure is not None:
            with transaction.atomic():
                # Recipients claimed by this tick but not handed over go back
                # to PENDING (no message exists for them).
                unsent = [r.pk for r in claimed[index + 1 :]]
                CampaignRecipient.objects.filter(
                    pk__in=unsent, status=R.QUEUED, message__isnull=True
                ).update(status=R.PENDING)
                locked = _locked(campaign.pk)
                if locked.status == S.RUNNING:
                    _fail_campaign(locked, "SEND_BLOCKED", failure)
            return TickResult(S.FAILED, dispatched)
        dispatched += 1

    with transaction.atomic():
        campaign = _locked(campaign.pk)
        if campaign.status != S.RUNNING:
            return TickResult(campaign.status, dispatched)
        remaining = CampaignRecipient.objects.filter(
            campaign=campaign, status__in=progress.NOT_DONE
        ).exists()
        if not remaining:
            campaign.status = S.COMPLETED
            campaign.completed_at = timezone.now()
            campaign.save(update_fields=["status", "completed_at", "updated_at"])
            record_event(campaign, "completed", **progress.counts(campaign.pk)["by_status"])
            progress.publish_updated(campaign)
            _notify_campaign_finished(
                campaign, notifications.KIND_CAMPAIGN_COMPLETED, "Campaign completed"
            )
            return TickResult(S.COMPLETED, dispatched)
        progress.publish_progress(campaign.pk, business_id=campaign.business_id, force=True)
        _schedule_tick(campaign.pk, countdown=tick_seconds)
    return TickResult(S.RUNNING, dispatched, next_in=tick_seconds)


def watchdog() -> int:
    """Re-kick RUNNING campaigns whose tick chain stopped (lost task, crash)."""
    cutoff = timezone.now() - timedelta(seconds=settings.CAMPAIGN_STALE_TICK_SECONDS)
    stale = list(
        Campaign.objects.filter(status=S.RUNNING, last_tick_at__lt=cutoff).values_list(
            "pk", flat=True
        )[:100]
    )
    for campaign_id in stale:
        if settings.CAMPAIGN_AUTO_TICK:
            from apps.campaigns.tasks import campaign_tick_task

            _safe_delay(campaign_tick_task, str(campaign_id))
    stuck = list(
        Campaign.objects.filter(status=S.QUEUED, updated_at__lt=cutoff).values_list(
            "pk", flat=True
        )[:100]
    )
    for campaign_id in stuck:
        from apps.campaigns.tasks import start_campaign_task

        _safe_delay(start_campaign_task, str(campaign_id))
    return len(stale) + len(stuck)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------


def pause(campaign_id: Any, *, actor: Any, request: Any = None) -> Campaign:
    with transaction.atomic():
        campaign = _locked(campaign_id)
        _require(campaign, {S.RUNNING, S.QUEUED}, "pause")
        campaign.status = S.PAUSED
        campaign.paused_at = timezone.now()
        campaign.save(update_fields=["status", "paused_at", "updated_at"])
        record_event(campaign, "paused", actor)
        _audit(actions.CAMPAIGN_PAUSED, campaign, actor, request)
        progress.publish_updated(campaign)
    return campaign


def resume(campaign_id: Any, *, actor: Any, request: Any = None) -> Campaign:
    with transaction.atomic():
        campaign = _locked(campaign_id)
        _require(campaign, {S.PAUSED, S.FAILED}, "resume")
        report = checks.evaluate(
            campaign, for_start=True, audience=campaign.snapshot_built_at is None
        )
        if not report.launchable:
            raise checks.CampaignNotLaunchableError(details=report.as_dict())
        campaign.failure_code = campaign.failure_reason = ""
        campaign.paused_at = None
        # Claimed-but-never-handed-over recipients (no message) are safe to
        # release now; their idempotency key still prevents a double send.
        CampaignRecipient.objects.filter(
            campaign=campaign, status=R.QUEUED, message__isnull=True
        ).update(status=R.PENDING)
        needs_snapshot = campaign.snapshot_built_at is None
        campaign.status = S.QUEUED if needs_snapshot else S.RUNNING
        if not needs_snapshot:
            campaign.last_tick_at = timezone.now()
        campaign.save(
            update_fields=[
                "status",
                "paused_at",
                "failure_code",
                "failure_reason",
                "last_tick_at",
                "updated_at",
            ]
        )
        record_event(campaign, "resumed", actor)
        _audit(actions.CAMPAIGN_RESUMED, campaign, actor, request)
        progress.publish_updated(campaign)
        if needs_snapshot:
            _schedule_start(campaign.pk)
        else:
            _schedule_tick(campaign.pk)
    return campaign


def cancel(campaign_id: Any, *, actor: Any, request: Any = None) -> dict[str, int]:
    with transaction.atomic():
        campaign = _locked(campaign_id)
        _require(campaign, {S.SCHEDULED, S.QUEUED, S.RUNNING, S.PAUSED, S.FAILED}, "cancel")
        now = timezone.now()
        campaign.status = S.CANCELLED
        campaign.cancelled_at = now
        campaign.save(update_fields=["status", "cancelled_at", "updated_at"])
        pending = CampaignRecipient.objects.filter(campaign=campaign, status=R.PENDING).update(
            status=R.CANCELLED, status_changed_at=now
        )
        orphaned = CampaignRecipient.objects.filter(
            campaign=campaign, status=R.QUEUED, message__isnull=True
        ).update(status=R.CANCELLED, status_changed_at=now)
        queued_messages = list(
            CampaignRecipient.objects.filter(
                campaign=campaign, status=R.QUEUED, message__isnull=False
            ).values_list("message_id", flat=True)
        )
    stopped = sum(
        cancel_queued_message(
            message_id,
            code=progress.CANCELLED_CODE,
            reason="The campaign was cancelled before sending.",
        )
        for message_id in queued_messages
    )
    with transaction.atomic():
        campaign = _locked(campaign_id)
        counts = {"pending_cancelled": pending + orphaned, "queued_stopped": stopped}
        record_event(campaign, "cancelled", actor, **counts)
        _audit(actions.CAMPAIGN_CANCELLED, campaign, actor, request, **counts)
        progress.publish_updated(campaign)
    return counts


def retry_failed(campaign_id: Any, *, actor: Any, request: Any = None) -> int:
    """Re-queue failed recipients whose failure is *safe* to retry (the
    provider certainly did not send). Timeouts with unknown outcome and
    permanent errors are never retried."""
    with transaction.atomic():
        campaign = _locked(campaign_id)
        _require(campaign, {S.RUNNING, S.PAUSED, S.COMPLETED, S.FAILED}, "retry")
        count = CampaignRecipient.objects.filter(
            campaign=campaign, status=R.FAILED, failure_category__in=RETRYABLE
        ).update(
            status=R.PENDING,
            attempts=F("attempts") + 1,
            status_changed_at=timezone.now(),
            error_code="",
            error_message="",
            failure_category="",
        )
        if count and campaign.status == S.COMPLETED:
            campaign.status = S.RUNNING
            campaign.completed_at = None
            campaign.last_tick_at = timezone.now()
            campaign.save(update_fields=["status", "completed_at", "last_tick_at", "updated_at"])
            _schedule_tick(campaign.pk)
        record_event(campaign, "retry_requested", actor, recipients=count)
        _audit(actions.CAMPAIGN_RETRY_REQUESTED, campaign, actor, request, recipients=count)
        progress.publish_updated(campaign)
    return count
