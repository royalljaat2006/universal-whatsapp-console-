"""Account/number webhook handling and the cost engine (real PostgreSQL)."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.billing.models import MessageCost, PricingMarket, PricingRule
from apps.billing.pricing import market_for_phone
from apps.billing.services import price_message, price_pending_messages
from apps.messaging.models import Direction, Message
from apps.messaging.state import MessageStatus
from apps.webhooks.models import WebhookEvent
from apps.whatsapp.models import ConnectionState, WhatsAppAccountEvent, WhatsAppBusinessAccount
from tests import factories
from tests.conftest import load_fixture

pytestmark = pytest.mark.django_db(transaction=True)


def _change(
    waba: Any, field: str, value: dict[str, Any], ts: int = 1_780_000_000
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": waba.external_id, "time": ts, "changes": [{"field": field, "value": value}]}
        ],
    }


class TestAccountWebhooks:
    def test_quality_update_matches_by_display_number_and_updates_tier(
        self, post_webhook: Any, waba: Any
    ) -> None:
        number = factories.make_phone_number(waba)
        digits = "".join(ch for ch in number.display_phone_number if ch.isdigit())
        post_webhook(
            _change(
                waba,
                "phone_number_quality_update",
                {
                    "display_phone_number": digits,
                    "event": "THROUGHPUT_UPGRADE",
                    "current_limit": "TIER_10K",
                    "old_limit": "TIER_2K",
                },
            )
        )
        number.refresh_from_db()
        assert number.messaging_limit_tier == "TIER_10K"
        event = WhatsAppAccountEvent.objects.get()
        assert event.phone_number_id == number.pk and event.event == "THROUGHPUT_UPGRADE"
        assert "TIER_2K → TIER_10K" in event.summary
        assert WebhookEvent.objects.get().status == WebhookEvent.Status.PROCESSED

    def test_account_alert_severity_and_phone_entity(self, post_webhook: Any, waba: Any) -> None:
        number = factories.make_phone_number(waba)
        post_webhook(
            _change(
                waba,
                "account_alerts",
                {
                    "entity_type": "PHONE_NUMBER",
                    "entity_id": number.external_id,
                    "alert_info": {
                        "alert_severity": "CRITICAL",
                        "alert_status": "ACTIVE",
                        "alert_type": "PROFILE_PICTURE_LOST",
                        "alert_description": "Profile picture removed",
                    },
                },
            )
        )
        event = WhatsAppAccountEvent.objects.get()
        assert event.severity == "critical" and event.phone_number_id == number.pk
        assert event.summary == "Profile picture removed"

    @pytest.mark.parametrize(
        ("event_name", "status", "connection"),
        [
            ("ACCOUNT_RESTRICTION", "restricted", "connected"),
            ("PARTNER_REMOVED", "active", "disconnected"),
            ("MM_LITE_TERMS_SIGNED", "active", "connected"),
        ],
    )
    def test_account_update_side_effects_only_for_documented_events(
        self, post_webhook: Any, waba: Any, event_name: str, status: str, connection: str
    ) -> None:
        post_webhook(_change(waba, "account_update", {"event": event_name}))
        waba.refresh_from_db()
        assert waba.status == status and waba.connection_state == connection

    def test_capability_update_is_stored(self, post_webhook: Any, waba: Any) -> None:
        value = {"max_daily_conversations_per_business": 2000, "max_phone_numbers_per_waba": 25}
        post_webhook(_change(waba, "business_capability_update", value))
        waba.refresh_from_db()
        assert waba.provider_metadata["capabilities"] == value

    def test_unknown_field_is_recorded_not_crashing(self, post_webhook: Any, waba: Any) -> None:
        post_webhook(_change(waba, "some_future_field", {"anything": [1, 2]}))
        assert WebhookEvent.objects.get().status == WebhookEvent.Status.PROCESSED
        assert WhatsAppAccountEvent.objects.get().field == "some_future_field"

    def test_duplicate_delivery_records_once(self, post_webhook: Any, waba: Any) -> None:
        payload = _change(waba, "account_update", {"event": "ACCOUNT_VIOLATION"})
        post_webhook(payload)
        post_webhook(payload)  # redelivery (deduplicated at the envelope)
        # A replay of the same change inside a new envelope is deduplicated too.
        from apps.webhooks.processor import mark_for_replay, process_event

        event = WebhookEvent.objects.get()
        mark_for_replay(event)
        process_event(event.pk, trigger="replay")
        assert WhatsAppAccountEvent.objects.filter(event="ACCOUNT_VIOLATION").count() == 1

    def test_events_api_is_business_scoped(
        self, post_webhook: Any, organization: Any, owner_api: Any
    ) -> None:
        kiosk_waba = factories.make_waba(
            organization, business=factories.make_business(organization, "Kiosk")
        )
        eps = factories.make_business(organization, "EPS")
        eps_waba = factories.make_waba(organization, business=eps)
        post_webhook(_change(kiosk_waba, "account_update", {"event": "ACCOUNT_RESTRICTION"}))
        post_webhook(_change(eps_waba, "account_update", {"event": "ACCOUNT_DELETED"}))
        rows = owner_api.get("/api/v1/whatsapp/events/", HTTP_X_BUSINESS_ID=str(eps.pk)).json()[
            "results"
        ]
        assert [r["event"] for r in rows] == ["ACCOUNT_DELETED"]
        assert (
            WhatsAppBusinessAccount.objects.get(pk=eps_waba.pk).connection_state
            == ConnectionState.DISCONNECTED
        )


# ---------------------------------------------------------------------------
# Cost engine
# ---------------------------------------------------------------------------


def _market(code: str, calling_codes: list[str], days_ago: int = 60) -> PricingMarket:
    return PricingMarket.objects.create(
        provider="meta",
        code=code,
        name=code,
        calling_codes=calling_codes,
        effective_from=timezone.now() - timedelta(days=days_ago),
        source="test rate card",
    )


def _rule(market: str, category: str, rate: str, days_ago: int = 60, **kw: Any) -> PricingRule:
    return PricingRule.objects.create(
        provider="meta",
        market=market,
        category=category,
        currency="INR",
        rate=Decimal(rate),
        effective_from=timezone.now() - timedelta(days=days_ago),
        source="test rate card",
        version="t1",
        **kw,
    )


def _delivered_message(phone: Any, *, billable: bool = True, category: str = "utility") -> Message:
    factories.open_service_window(phone, "919876543210")
    conversation = phone.conversations.get()
    now = timezone.now()
    return Message.objects.create(
        organization=phone.organization,
        conversation=conversation,
        phone_number=phone,
        contact=conversation.contact,
        direction=Direction.OUTBOUND,
        message_type="template",
        status=MessageStatus.DELIVERED,
        occurred_at=now,
        status_changed_at=now,
        delivered_at=now,
        billable=billable,
        pricing_category=category,
    )


class TestCosts:
    def test_market_uses_longest_calling_code_prefix(self) -> None:
        _market("REST_OF_ASIA", ["9"])
        india = _market("IN", ["91"])
        assert market_for_phone("meta", "919876543210", timezone.now()) == india
        assert market_for_phone("meta", "15551234567", timezone.now()) is None

    def test_billable_delivered_message_is_priced_with_snapshot(self, phone: Any) -> None:
        _market("IN", ["91"])
        rule = _rule("IN", "utility", "0.1150")
        message = _delivered_message(phone)
        assert price_message(message.pk) == "priced"
        cost = MessageCost.objects.get(message=message)
        assert cost.amount == Decimal("0.115000") and cost.currency == "INR"
        assert cost.pricing_snapshot["rule_id"] == str(rule.pk)
        assert cost.pricing_snapshot["market"] == "IN"
        assert price_message(message.pk) == "priced"  # idempotent
        assert MessageCost.objects.count() == 1

    def test_not_billable_is_free_without_rule(self, phone: Any) -> None:
        message = _delivered_message(phone, billable=False)
        assert price_message(message.pk) == "free"
        message.refresh_from_db()
        assert message.cost_status == "free" and not MessageCost.objects.exists()

    def test_missing_rate_is_pending_then_priced_when_card_loaded(self, phone: Any) -> None:
        _market("IN", ["91"])
        message = _delivered_message(phone)
        assert price_message(message.pk) == "pending_rate"
        _rule("IN", "utility", "0.20")
        assert price_pending_messages() == {"priced": 1}

    def test_historical_cost_is_immutable_when_rates_change(self, phone: Any) -> None:
        _market("IN", ["91"])
        old_rule = _rule("IN", "utility", "0.10", days_ago=60)
        message = _delivered_message(phone)
        price_message(message.pk)
        # A new rate card takes effect afterwards.
        PricingRule.objects.filter(pk=old_rule.pk).update(effective_to=timezone.now())
        _rule("IN", "utility", "0.90", days_ago=0)
        price_pending_messages()
        cost = MessageCost.objects.get(message=message)
        assert cost.amount == Decimal("0.100000")
        cost.amount = Decimal("1")
        with pytest.raises(TypeError):
            cost.save()

    def test_volume_tier_selection(self, phone: Any) -> None:
        _market("IN", ["91"])
        _rule("IN", "utility", "0.10", volume_tier_min=0, volume_tier_max=0)
        _rule("IN", "utility", "0.05", volume_tier_min=1)
        first, second = _delivered_message(phone), None
        price_message(first.pk)
        conversation = phone.conversations.get()
        now = timezone.now() + timedelta(seconds=1)
        second = Message.objects.create(
            organization=phone.organization,
            conversation=conversation,
            phone_number=phone,
            contact=conversation.contact,
            direction=Direction.OUTBOUND,
            message_type="template",
            status=MessageStatus.DELIVERED,
            occurred_at=now,
            status_changed_at=now,
            delivered_at=now,
            billable=True,
            pricing_category="utility",
        )
        price_message(second.pk)
        amounts = sorted(MessageCost.objects.values_list("amount", flat=True))
        assert amounts == [Decimal("0.050000"), Decimal("0.100000")]

    def test_status_webhook_triggers_costing(self, post_webhook: Any, phone: Any) -> None:
        _market("US", ["1"])
        _rule("US", "utility", "0.0034")
        factories.open_service_window(phone, "16505551234")
        conversation = phone.conversations.get()
        now = timezone.now()
        message = Message.objects.create(
            organization=phone.organization,
            conversation=conversation,
            phone_number=phone,
            contact=conversation.contact,
            direction=Direction.OUTBOUND,
            message_type="template",
            status=MessageStatus.ACCEPTED,
            external_id="wamid.COST",
            occurred_at=now,
            status_changed_at=now,
        )
        post_webhook(
            load_fixture(
                "status.json",
                WABA_ID=phone.business_account.external_id,
                PHONE_NUMBER_ID=phone.external_id,
                WAMID="wamid.COST",
                TS=str(int(now.timestamp()) + 5),
                STATUS="delivered",
            )
        )
        message.refresh_from_db()
        assert message.cost_status == "priced"
        assert MessageCost.objects.get(message=message).amount == Decimal("0.003400")

    def test_rate_card_commands(self, tmp_path: Path) -> None:
        markets = tmp_path / "markets.csv"
        markets.write_text(
            "code,name,calling_codes\nIN,India,91\nROA,Rest of Asia,93;94\n", encoding="utf-8"
        )
        rates = tmp_path / "inr.csv"
        rates.write_text(
            "market,category,rate\nIN,utility,0.12\nIN,marketing,0.78\n", encoding="utf-8"
        )
        call_command(
            "load_pricing_markets", file=str(markets), effective_from="2026-01-01", source="card v1"
        )
        call_command(
            "load_rate_card",
            file=str(rates),
            currency="INR",
            effective_from="2026-01-01",
            source="card v1",
            card_version="v1",
        )
        call_command(
            "load_rate_card",
            file=str(rates),
            currency="INR",
            effective_from="2026-10-01",
            source="card v2",
            card_version="v2",
        )
        assert PricingMarket.objects.count() == 2
        open_rules = PricingRule.objects.filter(effective_to__isnull=True)
        assert set(open_rules.values_list("version", flat=True)) == {"v2"}
        assert PricingRule.objects.filter(version="v1").exclude(effective_to=None).count() == 2
