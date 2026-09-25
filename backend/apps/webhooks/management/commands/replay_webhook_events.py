"""Replay raw webhook events through the current processor.

    python manage.py replay_webhook_events --id <uuid> [--id <uuid> ...]
    python manage.py replay_webhook_events --status dead --since 2026-09-01
    python manage.py replay_webhook_events --status partial --dry-run

Replays are safe: all domain writes are idempotent, so re-running an already
processed event only fills gaps (e.g. after a processor bug fix).
"""

from datetime import UTC, datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.webhooks.models import WebhookEvent
from apps.webhooks.processor import mark_for_replay, process_event


class Command(BaseCommand):
    help = "Replay stored webhook events through the processor."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--id", action="append", dest="ids", default=[])
        parser.add_argument("--status", choices=[s.value for s in WebhookEvent.Status])
        parser.add_argument("--since", help="ISO date/time (received_at >=)")
        parser.add_argument("--limit", type=int, default=1000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **opts: Any) -> None:
        qs = WebhookEvent.objects.all().order_by("received_at")
        if opts["ids"]:
            qs = qs.filter(pk__in=opts["ids"])
        elif opts["status"]:
            qs = qs.filter(status=opts["status"])
        else:
            raise CommandError("pass --id or --status")
        if opts["since"]:
            since = datetime.fromisoformat(opts["since"])
            if timezone.is_naive(since):
                since = since.replace(tzinfo=UTC)
            qs = qs.filter(received_at__gte=since)
        events = list(qs[: opts["limit"]])
        self.stdout.write(f"{len(events)} event(s) selected")
        if opts["dry_run"]:
            return
        for event in events:
            mark_for_replay(event)
            result = process_event(event.pk, trigger="replay")
            audit(
                actions.WEBHOOK_REPLAYED,
                resource=event,
                metadata={"result": result.status, "counts": result.counts},
            )
            self.stdout.write(f"{event.pk}: {result.status} {result.counts}")
