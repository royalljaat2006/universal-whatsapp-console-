"""Recalculate derived analytics from source messages and costs.

    python manage.py rebuild_analytics --from 2026-01-01 --to 2026-09-30
    python manage.py rebuild_analytics --business <uuid> --from … --to …

Idempotent: each (business, local day) is replaced atomically. Use it after
deploying Cycle 3 (to aggregate pre-existing history) or after a data fix.
"""

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.analytics.aggregation import rebuild
from apps.businesses.models import Business


class Command(BaseCommand):
    help = "Rebuild analytics rollups from source messages (idempotent)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--business", help="Business id (default: all businesses)")
        parser.add_argument("--from", dest="date_from", required=True)
        parser.add_argument("--to", dest="date_to", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            date_from = date.fromisoformat(options["date_from"])
            date_to = date.fromisoformat(options["date_to"])
        except ValueError as exc:
            raise CommandError("dates must be YYYY-MM-DD") from exc
        if date_to < date_from:
            raise CommandError("--to must not be before --from")
        businesses = Business.objects.select_related("organization")
        if options.get("business"):
            businesses = businesses.filter(pk=options["business"])
            if not businesses.exists():
                raise CommandError("business not found")
        total = 0
        for business in businesses:
            days = rebuild(business, date_from, date_to)
            total += days
            self.stdout.write(f"{business.name}: {days} day(s) recomputed")
        self.stdout.write(self.style.SUCCESS(f"done: {total} business-day(s)"))
