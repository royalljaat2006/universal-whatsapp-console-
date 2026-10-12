"""GET /api/v1/whatsapp/health/: scoped, permission-checked, derived only from
stored signals, and free of secrets and provider ids."""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.apikeys.services import create_api_key
from apps.contacts.services import ContactIdentifiers, resolve_contact
from apps.conversations.services import get_or_create_conversation
from apps.messaging.models import Message
from apps.messaging.state import MessageStatus
from apps.whatsapp.models import WhatsAppAccountEvent
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db

URL = "/api/v1/whatsapp/health/"


def _healthy(phone: Any) -> None:
    phone.quality_rating = "GREEN"
    phone.name_status = "APPROVED"
    phone.messaging_limit_tier = "TIER_1K"
    phone.last_synced_at = timezone.now()
    phone.save()


def _send(
    phone: Any, i: int, status: str, code: str = "", details: dict[str, Any] | None = None
) -> None:
    contact = resolve_contact(phone.business, ContactIdentifiers(phone_number=f"1650555{i:04d}"))
    conversation, _ = get_or_create_conversation(phone, contact)
    Message.objects.create(
        organization=phone.organization,
        conversation=conversation,
        phone_number=phone,
        contact=contact,
        direction="outbound",
        message_type="text",
        status=status,
        occurred_at=timezone.now(),
        status_changed_at=timezone.now(),
        error_code=code,
        error_details=details or {},
    )


class TestAccessAndScoping:
    def test_unauthenticated_is_refused(self) -> None:
        assert APIClient().get(URL).status_code in (401, 403)

    def test_an_api_key_without_the_numbers_scope_is_refused(
        self, owner: Any, organization: Any
    ) -> None:
        _, raw = create_api_key(
            organization=organization, created_by=owner, name="k", scopes=["contacts.view"]
        )
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw}", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        assert client.get(URL).status_code == 403

    def test_the_owner_sees_only_their_organizations_numbers(
        self, owner: Any, organization: Any
    ) -> None:
        mine = factories.make_phone_number(factories.make_waba(organization))
        other_org = factories.make_organization()
        theirs = factories.make_phone_number(factories.make_waba(other_org))
        body = api_client_for(owner, organization).get(URL).json()
        ids = {n["id"] for n in body["numbers"]}
        assert ids == {str(mine.pk)} and str(theirs.pk) not in json.dumps(body)

    def test_a_member_limited_to_one_business_never_sees_the_other(self, organization: Any) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        kiosk_phone = factories.make_phone_number(factories.make_waba(organization, business=kiosk))
        eps_phone = factories.make_phone_number(factories.make_waba(organization, business=eps))
        agent = factories.make_member(organization, "agent", businesses=[kiosk])

        body = api_client_for(agent, organization).get(URL).json()
        assert [n["id"] for n in body["numbers"]] == [str(kiosk_phone.pk)]
        assert str(eps_phone.pk) not in json.dumps(body) and "EPS" not in json.dumps(body)
        assert api_client_for(agent, organization, eps).get(URL).status_code in (403, 404)

    def test_the_selected_business_narrows_an_owners_view(
        self, owner: Any, organization: Any
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        factories.make_phone_number(factories.make_waba(organization, business=kiosk))
        eps_phone = factories.make_phone_number(factories.make_waba(organization, business=eps))
        body = api_client_for(owner, organization, eps).get(URL).json()
        assert [n["id"] for n in body["numbers"]] == [str(eps_phone.pk)]


class TestContent:
    def test_nothing_connected_is_unknown_with_a_plain_sentence(
        self, owner: Any, organization: Any
    ) -> None:
        body = api_client_for(owner, organization).get(URL).json()
        assert body["overall"] == "unknown"
        assert body["summary"] == "No WhatsApp account or number is connected yet."

    def test_no_secrets_and_no_provider_ids_are_returned(
        self, owner: Any, organization: Any
    ) -> None:
        waba = factories.make_waba(organization)
        phone = factories.make_phone_number(waba)
        text = json.dumps(api_client_for(owner, organization).get(URL).json())
        for forbidden in (
            waba.external_id,
            phone.external_id,
            waba.business_portfolio_external_id,
            "secret_reference",
            "token",
            "PROVIDER_SECRET",
        ):
            assert forbidden not in text, forbidden

    def test_a_healthy_number_and_account(self, owner: Any, organization: Any) -> None:
        waba = factories.make_waba(organization)
        now = timezone.now()
        for field, value in {
            "webhooks_subscribed_at": now,
            "account_review_status": "APPROVED",
            "business_verification_status": "verified",
            "template_sync_state": "succeeded",
            "templates_last_synced_at": now,
            "last_verified_at": now,
        }.items():
            setattr(waba, field, value)
        waba.save()
        _healthy(factories.make_phone_number(waba))
        body = api_client_for(owner, organization).get(URL).json()
        assert body["accounts"][0]["overall"] == "healthy"
        # No sends in 24h is "unknown" (nothing to measure), so the number is healthy, not "perfect".
        assert body["numbers"][0]["overall"] == "healthy"
        assert body["overall"] == "healthy"

    def test_a_red_quality_number_is_a_problem_pointing_to_meta(
        self, owner: Any, organization: Any
    ) -> None:
        phone = factories.make_phone_number(factories.make_waba(organization))
        phone.quality_rating = "RED"
        phone.save()
        body = api_client_for(owner, organization).get(URL).json()
        quality = next(c for c in body["numbers"][0]["checks"] if c["code"] == "quality")
        assert (quality["state"], quality["fix_in"], quality["basis"]) == (
            "problem",
            "meta",
            "provider",
        )
        assert body["overall"] == "problem"

    def test_the_response_states_it_is_not_a_live_check(
        self, owner: Any, organization: Any
    ) -> None:
        body = api_client_for(owner, organization).get(URL).json()
        assert any("not a live check" in n for n in body["notes"])


class TestRealDeliverySignal:
    def test_recent_failures_are_counted_and_explained_by_cause(
        self, owner: Any, organization: Any
    ) -> None:
        phone = factories.make_phone_number(factories.make_waba(organization))
        _healthy(phone)
        for i in range(6):
            _send(phone, i, MessageStatus.DELIVERED)
        for i in range(6, 12):
            _send(phone, i, MessageStatus.FAILED, code="131026")  # invalid recipient
        body = api_client_for(owner, organization).get(URL).json()
        check = next(c for c in body["numbers"][0]["checks"] if c["code"] == "recent_delivery")
        assert check["state"] == "problem"  # 6/12 = 50%
        assert "6 of 12" in check["detail"] and "invalid recipient" in check["detail"]
        assert check["basis"] == "console"

    def test_old_messages_and_other_numbers_do_not_count(
        self, owner: Any, organization: Any
    ) -> None:
        waba = factories.make_waba(organization)
        phone, other = factories.make_phone_number(waba), factories.make_phone_number(waba)
        _healthy(phone)
        for i in range(5):
            _send(other, i, MessageStatus.FAILED, code="131026")
        _send(phone, 50, MessageStatus.FAILED, code="131026")
        Message.objects.filter(phone_number=phone).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        body = api_client_for(owner, organization).get(URL).json()
        mine = next(n for n in body["numbers"] if n["id"] == str(phone.pk))
        check = next(c for c in mine["checks"] if c["code"] == "recent_delivery")
        assert check["state"] == "unknown"  # nothing recent from THIS number

    def test_a_provider_code_nested_in_details_is_used_for_the_cause(
        self, owner: Any, organization: Any
    ) -> None:
        phone = factories.make_phone_number(factories.make_waba(organization))
        _healthy(phone)
        for i in range(10):
            _send(
                phone,
                i,
                MessageStatus.FAILED,
                code="PROVIDER_REJECTED",
                details={"provider_code": "131056"},
            )
        body = api_client_for(owner, organization).get(URL).json()
        check = next(c for c in body["numbers"][0]["checks"] if c["code"] == "recent_delivery")
        assert "rate limit" in check["detail"]


class TestAlerts:
    def test_recent_warning_and_critical_events_are_listed_scoped_to_the_business(
        self, owner: Any, organization: Any
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        waba = factories.make_waba(organization, business=kiosk)
        for i, severity in enumerate(["info", "warning", "critical"]):
            WhatsAppAccountEvent.objects.create(
                organization=organization,
                business=kiosk,
                business_account=waba,
                field="account_update",
                event="X",
                severity=severity,
                summary=f"event {severity}",
                occurred_at=timezone.now() - timedelta(hours=i),
                dedupe_key=f"k{i}",
            )
        other_org = factories.make_organization()
        other_biz = factories.make_business(other_org)
        WhatsAppAccountEvent.objects.create(
            organization=other_org,
            business=other_biz,
            business_account=factories.make_waba(other_org, business=other_biz),
            field="account_update",
            event="X",
            severity="critical",
            summary="someone else's alert",
            occurred_at=timezone.now(),
            dedupe_key="other",
        )
        body = api_client_for(owner, organization).get(URL).json()
        assert {a["summary"] for a in body["recent_alerts"]} == {"event warning", "event critical"}


class TestFreshnessTimestamps:
    def test_rows_expose_when_meta_was_last_read_so_a_refresh_can_be_verified(
        self, owner: Any, organization: Any
    ) -> None:
        waba = factories.make_waba(organization)
        phone = factories.make_phone_number(waba)
        stamp = timezone.now() - timedelta(hours=2)
        type(waba).objects.filter(pk=waba.pk).update(last_verified_at=stamp)
        type(phone).objects.filter(pk=phone.pk).update(last_synced_at=None)
        body = api_client_for(owner, organization).get(URL).json()
        assert body["accounts"][0]["last_checked_at"] is not None
        assert body["numbers"][0]["last_checked_at"] is None  # never read: honest, not "now"
