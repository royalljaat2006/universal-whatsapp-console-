"""CSV writers for export jobs.

Each writer yields rows from a chunked database iterator, so memory stays flat
whatever the export size; the file is spooled to disk and stored in object
storage. Cells that a spreadsheet would treat as a formula are prefixed with
``'`` (CSV/formula injection).
"""

import csv
import io
import tempfile
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from apps.analytics.models import MessageMetricDaily
from apps.analytics.tracking import business_zone, day_bounds
from apps.billing.models import MessageCost
from apps.campaigns import reporting
from apps.campaigns.models import Campaign, CampaignRecipient
from apps.media import storage
from apps.messaging.models import Message
from apps.reports.models import ExportJob

CHUNK = 2000
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

Row = list[Any]


def safe(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _iso(value: Any) -> str:
    return value.isoformat() if value else ""


def _range(job: ExportJob) -> tuple[Any, Any]:
    tz = business_zone(job.business)
    start, _ = day_bounds(date.fromisoformat(job.params["date_from"]), tz)
    _, end = day_bounds(date.fromisoformat(job.params["date_to"]), tz)
    return start, end


def campaign_recipients(job: ExportJob) -> Iterator[Row]:
    campaign = Campaign.objects.get(pk=job.params["campaign_id"], business=job.business)
    yield [
        "recipient_id",
        "contact_id",
        "contact_name",
        "phone_number",
        "status",
        "attempts",
        "skip_reason",
        "failure_category",
        "error_code",
        "error_message",
        "message_id",
        "provider_message_id",
        "accepted_at",
        "sent_at",
        "delivered_at",
        "read_at",
        "cost_currency",
        "cost_amount",
    ]
    qs: QuerySet[CampaignRecipient] = (
        CampaignRecipient.objects.filter(campaign=campaign)
        .select_related("contact", "message", "message__cost")
        .order_by("pk")
    )
    for r in qs.iterator(chunk_size=CHUNK):
        m = r.message
        cost = getattr(m, "cost", None) if m else None
        yield [
            r.pk,
            r.contact_id,
            r.contact.display_name or r.contact.profile_name,
            r.phone_number,
            r.status,
            r.attempts,
            r.skip_reason,
            r.failure_category,
            r.error_code,
            r.error_message,
            m.pk if m else "",
            (m.external_id or "") if m else "",
            _iso(m.accepted_at) if m else "",
            _iso(m.sent_at) if m else "",
            _iso(m.delivered_at) if m else "",
            _iso(m.read_at) if m else "",
            cost.currency if cost else "",
            cost.amount if cost else "",
        ]


def campaign_summary(job: ExportJob) -> Iterator[Row]:
    campaign = Campaign.objects.select_related("business", "phone_number", "template").get(
        pk=job.params["campaign_id"], business=job.business
    )
    data = reporting.report(campaign)
    yield ["metric", "value"]
    info = data["campaign"]
    for key in ("name", "status", "business", "phone_number"):
        yield [key, info[key]]
    yield ["template", f"{info['template']['name']} ({info['template']['language']})"]
    for key, value in data["timing"].items():
        yield [key, value if value is not None else ""]
    for key, value in data["progress"].items():
        if key == "by_status":
            for status, count in value.items():
                yield [f"recipients_{status}", count]
        else:
            yield [key, value]
    for row in data["cost"]["by_currency"]:
        yield [f"cost_{row['currency']}", row["amount"]]
    yield ["cost_state", data["cost"]["state"]]
    for group in data["failures"]["groups"]:
        yield [f"failures_{group['category']}", group["count"]]


def messages(job: ExportJob) -> Iterator[Row]:
    start, end = _range(job)
    qs = Message.objects.filter(
        business=job.business, occurred_at__gte=start, occurred_at__lt=end
    ).select_related("phone_number", "contact", "template", "cost")
    if job.params.get("phone_number_id"):
        qs = qs.filter(phone_number_id=job.params["phone_number_id"])
    if job.params.get("direction"):
        qs = qs.filter(direction=job.params["direction"])
    if job.params.get("campaign_id"):
        qs = qs.filter(campaign_id=job.params["campaign_id"])
    yield [
        "message_id",
        "occurred_at",
        "direction",
        "type",
        "status",
        "business_number",
        "contact_phone",
        "template",
        "campaign_id",
        "error_code",
        "error_message",
        "cost_status",
        "cost_currency",
        "cost_amount",
    ]
    for m in qs.order_by("occurred_at", "pk").iterator(chunk_size=CHUNK):
        cost = getattr(m, "cost", None)
        yield [
            m.pk,
            _iso(m.occurred_at),
            m.direction,
            m.message_type,
            m.status,
            m.phone_number.display_phone_number,
            m.contact.phone_number,
            m.template.name if m.template else "",
            m.campaign_id or "",
            m.error_code,
            m.error_message,
            m.cost_status,
            cost.currency if cost else "",
            cost.amount if cost else "",
        ]


def costs(job: ExportJob) -> Iterator[Row]:
    start, end = _range(job)
    qs = MessageCost.objects.filter(
        message__business=job.business,
        message__occurred_at__gte=start,
        message__occurred_at__lt=end,
    ).select_related("message__phone_number", "message__template")
    yield [
        "message_id",
        "occurred_at",
        "priced_at",
        "business_number",
        "template",
        "campaign_id",
        "market",
        "category",
        "currency",
        "unit_rate",
        "amount",
        "rate_card_source",
        "rate_card_version",
    ]
    for c in qs.order_by("priced_at", "pk").iterator(chunk_size=CHUNK):
        m = c.message
        yield [
            m.pk,
            _iso(m.occurred_at),
            _iso(c.priced_at),
            m.phone_number.display_phone_number,
            m.template.name if m.template else "",
            m.campaign_id or "",
            c.market,
            c.category,
            c.currency,
            c.unit_rate,
            c.amount,
            c.pricing_snapshot.get("rule_source", ""),
            c.pricing_snapshot.get("rule_version", ""),
        ]


def analytics_daily(job: ExportJob) -> Iterator[Row]:
    qs = MessageMetricDaily.objects.filter(
        business=job.business,
        date__gte=job.params["date_from"],
        date__lte=job.params["date_to"],
    ).select_related("phone_number", "template", "campaign")
    counters = [
        "inbound",
        "outbound",
        "in_flight",
        "accepted",
        "sent",
        "delivered",
        "read",
        "failed",
        "rejected",
        "templates_sent",
        "media_sent",
        "media_received",
        "cost_priced",
        "cost_pending_rate",
        "cost_free",
    ]
    yield ["date", "business_number", "template", "campaign", *counters]
    for row in qs.order_by("date", "phone_number_id").iterator(chunk_size=CHUNK):
        yield [
            row.date.isoformat(),
            row.phone_number.display_phone_number,
            row.template.name if row.template else "",
            row.campaign.name if row.campaign else "",
            *(getattr(row, name) for name in counters),
        ]


WRITERS: dict[str, Callable[[ExportJob], Iterator[Row]]] = {
    ExportJob.Kind.CAMPAIGN_RECIPIENTS: campaign_recipients,
    ExportJob.Kind.CAMPAIGN_SUMMARY: campaign_summary,
    ExportJob.Kind.MESSAGES: messages,
    ExportJob.Kind.COSTS: costs,
    ExportJob.Kind.ANALYTICS_DAILY: analytics_daily,
}


def run(job_id: Any) -> str:
    job = ExportJob.objects.select_related("business__organization").get(pk=job_id)
    if job.status not in {ExportJob.Status.PENDING, ExportJob.Status.RUNNING}:
        return job.status
    job.status, job.started_at = ExportJob.Status.RUNNING, timezone.now()
    job.save(update_fields=["status", "started_at", "updated_at"])
    try:
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            writer = csv.writer(text)
            rows = -1  # header
            for row in WRITERS[job.kind](job):
                writer.writerow([safe(v) for v in row])
                rows += 1
            text.flush()
            text.detach()
            raw.seek(0)
            key = f"exports/org/{job.organization_id}/biz/{job.business_id}/{job.pk}.csv"
            stored = storage.save(key, raw)
    except Exception as exc:
        job.status, job.error = ExportJob.Status.FAILED, str(exc)[:500]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])
        raise
    job.status = ExportJob.Status.SUCCEEDED
    job.row_count = max(rows, 0)
    job.file_size = stored.size
    job.storage_key = stored.key
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "row_count",
            "file_size",
            "storage_key",
            "finished_at",
            "updated_at",
        ]
    )
    return job.status
