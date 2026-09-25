"""Cycle 3 analytics: rollups are derived from real messages/costs and every
read is business-scoped."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.analytics.aggregation import process_dirty_buckets, recompute_business_day
from apps.analytics.models import (
    AnalyticsDirtyBucket,
    ContactActivityDaily,
    CostMetricDaily,
    MessageMetricDaily,
    MessageMetricHourly,
)
from apps.billing.models import MessageCost
from apps.contacts.services import ContactIdentifiers, resolve_contact
from apps.conversations.services import get_or_create_conversation
from apps.messaging.models import Message
from apps.messaging.state import MessageStatus
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db

IST = ZoneInfo("Asia/Kolkata")
DAY = datetime(2026, 9, 20, 12, 0, tzinfo=IST)


def _message(
    phone: Any,
    wa_id: str,
    *,
    direction: str = "outbound",
    status: str = MessageStatus.DELIVERED,
    at: datetime = DAY,
    template: Any = None,
    message_type: str | None = None,
    campaign: Any = None,
    **fields: Any,
) -> Message:
    contact = resolve_contact(phone.business, ContactIdentifiers(phone_number=wa_id))
    conversation, _ = get_or_create_conversation(phone, contact)
    stamps: dict[str, Any] = {}
    if direction == "outbound":
        order = [
            MessageStatus.ACCEPTED,
            MessageStatus.SENT,
            MessageStatus.DELIVERED,
            MessageStatus.READ,
        ]
        reached = order.index(status) + 1 if status in order else 0
        for name, index in (
            ("accepted_at", 1),
            ("sent_at", 2),
            ("delivered_at", 3),
            ("read_at", 4),
        ):
            if reached >= index:
                stamps[name] = at + timedelta(seconds=index)
        if status in order:
            stamps["external_id"] = f"wamid.{wa_id}.{at.timestamp()}.{status}.{template}"
    return Message.objects.create(
        organization=phone.organization,
        conversation=conversation,
        phone_number=phone,
        contact=contact,
        direction=direction,
        message_type=message_type or ("template" if template else "text"),
        status=MessageStatus.RECEIVED if direction == "inbound" else status,
        template=template,
        campaign=campaign,
        occurred_at=at,
        status_changed_at=at,
        **{**stamps, **fields},
    )


@pytest.fixture
def world(owner: Any, organization: Any) -> dict[str, Any]:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")
    for business in (kiosk, eps):
        business.timezone = "Asia/Kolkata"
        business.save(update_fields=["timezone"])
    kiosk_waba = factories.make_waba(organization, business=kiosk)
    eps_waba = factories.make_waba(organization, business=eps)
    return {
        "kiosk": kiosk,
        "eps": eps,
        "kiosk_phone": factories.make_phone_number(kiosk_waba),
        "kiosk_phone_2": factories.make_phone_number(kiosk_waba),
        "eps_phone": factories.make_phone_number(eps_waba),
        "kiosk_template": factories.make_campaign_template(kiosk_waba),
        "api": api_client_for(owner, organization, kiosk),
    }


def _seed(world: dict[str, Any]) -> None:
    phone, template = world["kiosk_phone"], world["kiosk_template"]
    _message(phone, "919800000001", status=MessageStatus.READ, template=template)
    _message(phone, "919800000002", status=MessageStatus.DELIVERED, template=template)
    _message(phone, "919800000003", status=MessageStatus.FAILED, error_code="131026")
    _message(phone, "919800000004", status=MessageStatus.QUEUED)
    _message(phone, "919800000001", direction="inbound")
    _message(phone, "919800000001", direction="inbound", message_type="image")
    _message(world["kiosk_phone_2"], "919800000009", status=MessageStatus.SENT)
    _message(world["eps_phone"], "919800000001", status=MessageStatus.DELIVERED)


class TestAggregation:
    def test_counters_are_derived_from_messages(self, world: dict[str, Any]) -> None:
        _seed(world)
        result = recompute_business_day(world["kiosk"], DAY.date())
        assert result.daily_rows == 3  # (phone1,template) (phone1,none) (phone2,none)
        rows = MessageMetricDaily.objects.filter(business=world["kiosk"], date=DAY.date())
        total = {
            f: sum(getattr(r, f) for r in rows)
            for f in (
                "inbound",
                "outbound",
                "in_flight",
                "sent",
                "delivered",
                "read",
                "failed",
                "rejected",
                "templates_sent",
                "media_received",
            )
        }
        assert total == {
            "inbound": 2,
            "outbound": 5,
            "in_flight": 1,
            "sent": 3,
            "delivered": 2,
            "read": 1,
            "failed": 1,
            "rejected": 1,
            "templates_sent": 2,
            "media_received": 1,
        }
        # EPS messages never land in Kiosk rollups.
        assert not MessageMetricDaily.objects.filter(
            business=world["kiosk"], phone_number=world["eps_phone"]
        ).exists()
        assert (
            ContactActivityDaily.objects.filter(business=world["kiosk"])
            .values("contact_id")
            .distinct()
            .count()
            == 5
        )
        assert MessageMetricHourly.objects.filter(business=world["kiosk"], date=DAY.date()).exists()

    def test_recompute_is_idempotent_and_converges_after_late_webhooks(
        self, world: dict[str, Any]
    ) -> None:
        _seed(world)
        recompute_business_day(world["kiosk"], DAY.date())
        recompute_business_day(world["kiosk"], DAY.date())
        assert (
            sum(r.outbound for r in MessageMetricDaily.objects.filter(business=world["kiosk"])) == 5
        )
        # A late "read" for a delivered message is reflected, not double counted.
        message = Message.objects.get(contact__phone_number="919800000002", direction="outbound")
        message.read_at, message.status = DAY + timedelta(hours=3), MessageStatus.READ
        message.save()
        recompute_business_day(world["kiosk"], DAY.date())
        rows = MessageMetricDaily.objects.filter(business=world["kiosk"])
        assert sum(r.read for r in rows) == 2 and sum(r.delivered for r in rows) == 2

    def test_buckets_use_business_local_days(self, world: dict[str, Any]) -> None:
        # 20:00 UTC on the 19th is 01:30 on the 20th in India.
        late = datetime(2026, 9, 19, 20, 0, tzinfo=ZoneInfo("UTC"))
        _message(world["kiosk_phone"], "919800000010", at=late)
        recompute_business_day(world["kiosk"], DAY.date())
        recompute_business_day(world["kiosk"], DAY.date() - timedelta(days=1))
        assert (
            MessageMetricDaily.objects.get(business=world["kiosk"], date=DAY.date()).outbound == 1
        )
        assert not MessageMetricDaily.objects.filter(
            business=world["kiosk"], date=DAY.date() - timedelta(days=1)
        ).exists()

    def test_message_changes_mark_buckets_dirty(
        self, world: dict[str, Any], post_webhook: Any, django_capture_on_commit_callbacks: Any
    ) -> None:
        from tests.conftest import load_fixture

        phone = world["kiosk_phone"]
        with django_capture_on_commit_callbacks(execute=True):
            post_webhook(
                load_fixture(
                    "inbound_text.json",
                    WABA_ID=phone.business_account.external_id,
                    PHONE_NUMBER_ID=phone.external_id,
                    WAMID="wamid.DIRTY",
                    TS=str(int(timezone.now().timestamp())),
                )
            )
        assert AnalyticsDirtyBucket.objects.filter(business_id=world["kiosk"].pk).exists()
        assert process_dirty_buckets() >= 1
        assert not AnalyticsDirtyBucket.objects.exists()
        today = timezone.now().astimezone(IST).date()
        assert MessageMetricDaily.objects.get(business=world["kiosk"], date=today).inbound == 1


class TestAnalyticsApi:
    def _get(self, world: dict[str, Any], path: str, client: Any = None, **params: str) -> Any:
        query = {"date_from": DAY.date().isoformat(), "date_to": DAY.date().isoformat(), **params}
        return (client or world["api"]).get(f"/api/v1/analytics/{path}/", query)

    def test_overview_rates_and_no_data(self, world: dict[str, Any]) -> None:
        empty = self._get(world, "overview").json()
        assert empty["has_data"] is False
        assert empty["rates"]["delivery_rate"] is None  # "no data", never 0 %
        _seed(world)
        recompute_business_day(world["kiosk"], DAY.date())
        data = self._get(world, "overview").json()
        assert data["has_data"] is True
        assert data["totals"]["outbound"] == 5 and data["totals"]["inbound"] == 2
        assert data["rates"]["delivery_rate"] == pytest.approx(2 / 4)  # in-flight excluded
        assert data["rates"]["failure_rate"] == pytest.approx(1 / 4)
        assert data["unique_contacts"] == 5
        assert data["active_conversations"] == 5
        assert data["cost"]["state"] == "none"
        assert data["range"]["timezone"] == "Asia/Kolkata"

    def test_date_filter_excludes_other_days(self, world: dict[str, Any]) -> None:
        _seed(world)
        _message(world["kiosk_phone"], "919800000020", at=DAY - timedelta(days=3))
        for offset in (0, 3):
            recompute_business_day(world["kiosk"], (DAY - timedelta(days=offset)).date())
        assert self._get(world, "overview").json()["totals"]["outbound"] == 5
        wide = self._get(
            world, "overview", date_from=(DAY - timedelta(days=6)).date().isoformat()
        ).json()
        assert wide["totals"]["outbound"] == 6
        series = self._get(
            world, "timeseries", date_from=(DAY - timedelta(days=6)).date().isoformat()
        ).json()
        assert series["granularity"] == "day" and len(series["points"]) == 7
        hourly = self._get(world, "timeseries", granularity="hour").json()
        assert len(hourly["points"]) == 24
        assert sum(p["outbound"] for p in hourly["points"]) == 5

    def test_business_and_number_isolation(self, world: dict[str, Any], organization: Any) -> None:
        _seed(world)
        for business in (world["kiosk"], world["eps"]):
            recompute_business_day(business, DAY.date())
        agent = factories.make_member(organization, "manager", businesses=[world["kiosk"]])
        kiosk_only = api_client_for(agent, organization)
        totals = self._get(world, "overview", client=kiosk_only).json()["totals"]
        assert totals["outbound"] == 5  # EPS's delivered message is not included
        foreign = self._get(
            world, "overview", client=kiosk_only, phone_number=str(world["eps_phone"].pk)
        )
        assert foreign.status_code == 404
        eps_header = api_client_for(agent, organization, world["eps"])
        assert self._get(world, "overview", client=eps_header).status_code == 404
        numbers = self._get(world, "numbers", client=kiosk_only).json()["numbers"]
        assert {n["phone_number_id"] for n in numbers} == {
            str(world["kiosk_phone"].pk),
            str(world["kiosk_phone_2"].pk),
        }
        one = self._get(world, "overview", phone_number=str(world["kiosk_phone_2"].pk)).json()
        assert one["totals"]["outbound"] == 1

    def test_template_metrics(self, world: dict[str, Any]) -> None:
        _seed(world)
        recompute_business_day(world["kiosk"], DAY.date())
        [row] = self._get(world, "templates").json()["templates"]
        assert row["template_id"] == str(world["kiosk_template"].pk)
        assert row["sends"] == 2 and row["totals"]["read"] == 1
        assert row["unique_recipients"] == 2
        assert row["first_used"] == DAY.date().isoformat()
        filtered = self._get(world, "overview", template=str(world["kiosk_template"].pk)).json()
        assert filtered["totals"]["outbound"] == 2

    def test_cost_metrics_come_from_message_costs(self, world: dict[str, Any]) -> None:
        _seed(world)
        delivered = Message.objects.filter(
            business=world["kiosk"], direction="outbound", delivered_at__isnull=False
        )
        for message in delivered:
            MessageCost.objects.create(
                organization=message.organization,
                message=message,
                provider="meta",
                market="IN",
                category="utility",
                billable=True,
                currency="INR",
                unit_rate=Decimal("0.115"),
                amount=Decimal("0.115"),
                priced_at=timezone.now(),
            )
            Message.objects.filter(pk=message.pk).update(cost_status=Message.CostStatus.PRICED)
        pending = _message(world["kiosk_phone"], "919800000030", status=MessageStatus.DELIVERED)
        Message.objects.filter(pk=pending.pk).update(cost_status=Message.CostStatus.PENDING_RATE)
        recompute_business_day(world["kiosk"], DAY.date())
        cost = self._get(world, "overview").json()["cost"]
        assert cost["state"] == "partial"
        [inr] = cost["by_currency"]
        assert inr["currency"] == "INR" and Decimal(inr["amount"]) == Decimal("0.23")
        assert inr["priced_messages"] == 2 and Decimal(inr["average_per_message"]) == Decimal(
            "0.115"
        )
        assert cost["pending_rate_messages"] == 1
        by_number = self._get(world, "costs", group_by="phone_number").json()["rows"]
        assert by_number[0]["label"] == world["kiosk_phone"].display_phone_number
        assert CostMetricDaily.objects.filter(business=world["kiosk"]).count() == 1
        assert self._get(world, "costs", group_by="bogus").status_code == 400

    def test_failures_grouped_by_cause(self, world: dict[str, Any]) -> None:
        _seed(world)
        data = self._get(world, "failures").json()
        assert data["total"] == 1
        assert data["groups"][0]["category"] == "invalid_recipient"
        assert data["recent"][0]["error_code"] == "131026"

    def test_permission_required(self, world: dict[str, Any], organization: Any) -> None:
        agent = factories.make_member(organization, "agent", businesses=[world["kiosk"]])
        client = api_client_for(agent, organization, world["kiosk"])
        assert self._get(world, "overview", client=client).status_code == 403
