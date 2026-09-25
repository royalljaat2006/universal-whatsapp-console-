"""Load rate-card markets (calling code -> market) from a CSV transcribed from
Meta's published rate card. Rows are appended as a new effective version;
older versions are closed at ``--effective-from`` (never edited in place).

CSV columns: code,name,calling_codes   (calling_codes separated by ';')

    python manage.py load_pricing_markets --file markets.csv \\
        --effective-from 2026-10-01 --source "Meta rate card 2026-10-01"
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from apps.billing.models import PricingMarket


def parse_start(raw: str) -> datetime:
    value = datetime.fromisoformat(raw)
    return (
        value
        if timezone.is_aware(value)
        else timezone.make_aware(value, timezone.get_default_timezone())
    )


class Command(BaseCommand):
    help = "Load rate-card markets (calling codes) from CSV."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--file", required=True)
        parser.add_argument("--provider", default="meta")
        parser.add_argument("--effective-from", required=True)
        parser.add_argument("--source", required=True)

    @transaction.atomic
    def handle(self, *args: Any, **opts: Any) -> None:
        start = parse_start(opts["effective_from"])
        path = Path(opts["file"])
        if not path.exists():
            raise CommandError(f"{path} not found")
        created = 0
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                code = (row.get("code") or "").strip()
                codes = [
                    c.strip().lstrip("+")
                    for c in (row.get("calling_codes") or "").split(";")
                    if c.strip()
                ]
                if not code or not codes or not all(c.isdigit() for c in codes):
                    raise CommandError(f"invalid row: {row}")
                PricingMarket.objects.filter(
                    provider=opts["provider"],
                    code=code,
                    effective_to__isnull=True,
                    effective_from__lt=start,
                ).update(effective_to=start)
                PricingMarket.objects.update_or_create(
                    provider=opts["provider"],
                    code=code,
                    effective_from=start,
                    defaults={
                        "name": (row.get("name") or code).strip(),
                        "calling_codes": codes,
                        "source": opts["source"],
                    },
                )
                created += 1
        self.stdout.write(
            self.style.SUCCESS(f"loaded {created} market(s) effective {start.isoformat()}")
        )
