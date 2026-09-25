"""Load a provider rate card from a CSV transcribed from Meta's published rate
card for the billing currency. New rules take effect at ``--effective-from``;
open rules for the same key are closed at that instant. Existing MessageCost
rows are never touched — historical costs stay as priced.

CSV columns: market,category,rate[,volume_tier_min,volume_tier_max]
(category as reported in status webhooks: marketing, utility,
authentication, authentication-international, …)

    python manage.py load_rate_card --file inr.csv --currency INR \\
        --effective-from 2026-10-01 --source "Meta rate card INR 2026-10-01" --card-version 2026-10
"""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.audit import actions
from apps.audit.services import record as audit
from apps.billing.management.commands.load_pricing_markets import parse_start
from apps.billing.models import PricingRule


def _int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


class Command(BaseCommand):
    help = "Load provider pricing rules from a rate-card CSV."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--file", required=True)
        parser.add_argument("--provider", default="meta")
        parser.add_argument("--currency", required=True)
        parser.add_argument("--effective-from", required=True)
        parser.add_argument("--source", required=True)
        parser.add_argument("--card-version", default="")

    @transaction.atomic
    def handle(self, *args: Any, **opts: Any) -> None:
        start = parse_start(opts["effective_from"])
        currency = opts["currency"].upper()
        if len(currency) != 3:
            raise CommandError("currency must be an ISO 4217 code")
        path = Path(opts["file"])
        if not path.exists():
            raise CommandError(f"{path} not found")
        loaded = 0
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    rate = Decimal((row.get("rate") or "").strip())
                except InvalidOperation as exc:
                    raise CommandError(f"invalid rate in row {row}") from exc
                market = (row.get("market") or "").strip()
                category = (row.get("category") or "").strip().lower()
                if not market or not category or rate < 0:
                    raise CommandError(f"invalid row: {row}")
                tier_min, tier_max = _int(row.get("volume_tier_min")), _int(
                    row.get("volume_tier_max")
                )
                PricingRule.objects.filter(
                    provider=opts["provider"],
                    market=market,
                    category=category,
                    currency=currency,
                    organization__isnull=True,
                    business__isnull=True,
                    business_account__isnull=True,
                    volume_tier_min=tier_min,
                    volume_tier_max=tier_max,
                    effective_to__isnull=True,
                    effective_from__lt=start,
                ).update(effective_to=start)
                PricingRule.objects.create(
                    provider=opts["provider"],
                    market=market,
                    category=category,
                    currency=currency,
                    rate=rate,
                    effective_from=start,
                    volume_tier_min=tier_min,
                    volume_tier_max=tier_max,
                    source=opts["source"],
                    version=opts["card_version"],
                )
                loaded += 1
        audit(
            actions.PRICING_RULE_CHANGED,
            metadata={
                "source": opts["source"],
                "version": opts["card_version"],
                "currency": currency,
                "rules": loaded,
                "effective_from": start.isoformat(),
            },
        )
        self.stdout.write(
            self.style.SUCCESS(f"loaded {loaded} rule(s) effective {start.isoformat()}")
        )
