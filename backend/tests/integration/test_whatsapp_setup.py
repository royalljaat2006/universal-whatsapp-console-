"""Setup readiness (GET /whatsapp/setup/): every state comes from stored evidence,
never from a request having been made. Embedded-signup `state` protection."""

import json
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.apikeys.services import create_api_key
from apps.messaging.models import Message
from apps.whatsapp.models import SyncState
from tests import factories
from tests.conftest import api_client_for, load_fixture
from tests.fakes import FakeGraph, graph_error
from tests.integration.test_webhook_pipeline import ids

pytestmark = pytest.mark.django_db(transaction=True)

URL = "/api/v1/whatsapp/setup/"


@pytest.fixture(autouse=True)
def public_url(settings: Any) -> None:
    settings.PUBLIC_BASE_URL = "https://console.example.com"


def step(body: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in body["steps"] if s["id"] == step_id)


def verified_account(organization: Any, **extra: Any) -> tuple[Any, Any]:
    waba = factories.make_waba(organization)
    waba.last_verified_at = timezone.now()
    waba.webhooks_subscribed_at = timezone.now()
    waba.sync_state = SyncState.SUCCEEDED
    waba.template_sync_state = SyncState.SUCCEEDED
    for key, value in extra.items():
        setattr(waba, key, value)
    waba.save()
    phone = factories.make_phone_number(waba)
    return waba, phone


class TestAccessAndScoping:
    def test_unauthenticated_is_refused(self) -> None:
        assert APIClient().get(URL).status_code in (401, 403)

    def test_an_api_key_without_numbers_scope_is_refused(
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

    def test_other_organizations_data_never_appears(
        self, owner_api: Any, organization: Any
    ) -> None:
        _, mine = verified_account(organization)
        other = factories.make_organization()
        _, theirs = verified_account(other)
        body = owner_api.get(URL).json()
        assert [n["id"] for n in body["numbers"]] == [str(mine.pk)]
        assert str(theirs.pk) not in json.dumps(body)

    def test_a_member_limited_to_one_business_sees_only_that_business(
        self, organization: Any
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        kiosk_phone = factories.make_phone_number(factories.make_waba(organization, business=kiosk))
        eps_phone = factories.make_phone_number(factories.make_waba(organization, business=eps))
        agent = factories.make_member(organization, "agent", businesses=[kiosk])
        body = api_client_for(agent, organization).get(URL).json()
        assert [n["id"] for n in body["numbers"]] == [str(kiosk_phone.pk)]
        assert str(eps_phone.pk) not in json.dumps(body)

    def test_no_secrets_and_no_provider_ids_in_the_response(
        self, owner_api: Any, organization: Any
    ) -> None:
        waba, phone = verified_account(organization)
        raw = json.dumps(owner_api.get(URL).json(), default=str)
        for secret in (phone.external_id, waba.external_id, "test-app-secret", "test-verify-token"):
            assert secret not in raw


class TestSteps:
    def test_nothing_connected_is_not_started_and_nothing_is_verified(self, owner_api: Any) -> None:
        body = owner_api.get(URL).json()
        assert body["overall"] == "not_started"
        assert step(body, "connection")["state"] == "pending"
        assert step(body, "connection")["action"] == "connect"
        assert all(s["state"] != "verified" for s in body["steps"])

    def test_pending_connection_is_pending_not_failed(
        self, owner_api: Any, organization: Any
    ) -> None:
        waba = factories.make_waba(organization)
        waba.connection_state = "pending"
        waba.save()
        s = step(owner_api.get(URL).json(), "connection")
        assert s["state"] == "pending" and s["action"] == "refresh"

    def test_a_failed_connection_is_failed_with_meta_s_reason_in_plain_words(
        self, owner_api: Any, organization: Any
    ) -> None:
        waba = factories.make_waba(organization)
        waba.connection_state = "error"
        waba.connection_error = "The access token has expired."
        waba.connection_error_code = "190"
        waba.save()
        body = owner_api.get(URL).json()
        s = step(body, "connection")
        assert s["state"] == "failed" and "access token has expired" in s["detail"]
        assert body["overall"] == "blocked" and body["next_step"] == "connection"
        assert step(body, "numbers")["state"] == "unknown"  # not guessed while disconnected

    def test_a_connected_account_with_a_connected_number_verifies_both(
        self, owner_api: Any, organization: Any
    ) -> None:
        verified_account(organization)
        body = owner_api.get(URL).json()
        assert step(body, "connection")["state"] == "verified"
        assert step(body, "numbers")["state"] == "verified"

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("pending", "pending"),
            ("flagged", "failed"),
            ("restricted", "failed"),
            ("disconnected", "failed"),
        ],
    )
    def test_number_states_follow_what_meta_reported(
        self, owner_api: Any, organization: Any, status: str, expected: str
    ) -> None:
        _, phone = verified_account(organization)
        phone.status = status
        phone.save()
        assert step(owner_api.get(URL).json(), "numbers")["state"] == expected

    def test_a_synced_account_with_no_numbers_says_to_add_one_in_meta(
        self, owner_api: Any, organization: Any
    ) -> None:
        waba = factories.make_waba(organization)
        waba.sync_state = SyncState.SUCCEEDED
        waba.save()
        s = step(owner_api.get(URL).json(), "numbers")
        assert s["state"] == "pending" and s["fix_in"] == "meta"

    def test_a_failed_number_sync_is_failed(self, owner_api: Any, organization: Any) -> None:
        waba = factories.make_waba(organization)
        waba.sync_state = SyncState.FAILED
        waba.sync_error = "boom"
        waba.save()
        assert step(owner_api.get(URL).json(), "numbers")["state"] == "failed"


class TestWebhookEvidence:
    def test_subscription_comes_from_what_meta_reported(
        self, owner_api: Any, organization: Any
    ) -> None:
        waba, _ = verified_account(organization)
        assert step(owner_api.get(URL).json(), "webhook_subscription")["state"] == "verified"

        waba.webhooks_subscribed_at = None
        waba.save()
        assert step(owner_api.get(URL).json(), "webhook_subscription")["state"] == "failed"

        waba.last_verified_at = None
        waba.save()
        assert step(owner_api.get(URL).json(), "webhook_subscription")["state"] == "unknown"

    def test_delivery_is_pending_until_a_signed_event_really_arrives(
        self, owner_api: Any, organization: Any, post_webhook: Any
    ) -> None:
        _, phone = verified_account(organization)
        before = step(owner_api.get(URL).json(), "webhook_delivery")
        assert before["state"] == "pending" and before["evidence"]["events_received"] == 0

        payload = load_fixture("inbound_text.json", **ids(phone))
        assert post_webhook(payload, signature="sha256=forged").status_code == 401
        still = step(owner_api.get(URL).json(), "webhook_delivery")
        assert still["state"] == "pending"  # an unsigned request is NOT evidence

        assert post_webhook(payload).status_code == 200
        after = step(owner_api.get(URL).json(), "webhook_delivery")
        assert after["state"] == "verified"
        assert after["evidence"]["events_received"] == 1 and after["evidence"]["last_received_at"]

    def test_a_duplicate_delivery_counts_once(
        self, owner_api: Any, organization: Any, post_webhook: Any
    ) -> None:
        _, phone = verified_account(organization)
        payload = load_fixture("inbound_text.json", **ids(phone))
        post_webhook(payload)
        post_webhook(payload)
        assert (
            step(owner_api.get(URL).json(), "webhook_delivery")["evidence"]["events_received"] == 1
        )

    def test_another_tenants_events_are_not_my_evidence(
        self, owner_api: Any, organization: Any, post_webhook: Any
    ) -> None:
        verified_account(organization)
        other = factories.make_organization()
        _, theirs = verified_account(other)
        post_webhook(load_fixture("inbound_text.json", **ids(theirs)))
        assert (
            step(owner_api.get(URL).json(), "webhook_delivery")["evidence"]["events_received"] == 0
        )

    def test_events_that_arrive_but_cannot_be_processed_are_failed(
        self, owner_api: Any, organization: Any, post_webhook: Any
    ) -> None:
        from apps.webhooks.models import WebhookEvent

        _, phone = verified_account(organization)
        post_webhook(load_fixture("inbound_text.json", **ids(phone)))
        WebhookEvent.objects.update(status="dead")
        assert step(owner_api.get(URL).json(), "webhook_delivery")["state"] == "failed"

    def test_server_webhook_settings_are_checked_and_never_expose_secrets(
        self, owner_api: Any, organization: Any, settings: Any
    ) -> None:
        verified_account(organization)
        body = owner_api.get(URL).json()
        assert body["server"]["state"] == "verified"
        assert (
            body["webhook"]["callback_url"]
            == "https://console.example.com/webhooks/v1/meta/whatsapp/"
        )
        assert body["capabilities"]["callback_check"]["state"] == "unsupported"

        settings.META_WEBHOOK_VERIFY_TOKEN = ""
        failed = owner_api.get(URL).json()
        assert (
            failed["server"]["state"] == "failed" and "verify token" in failed["server"]["detail"]
        )
        assert failed["overall"] == "blocked"

        settings.META_WEBHOOK_VERIFY_TOKEN = "t"
        settings.PUBLIC_BASE_URL = "http://localhost:8080"
        assert owner_api.get(URL).json()["server"]["state"] == "failed"

    def test_embedded_signup_not_configured_is_unsupported_not_hidden(
        self, owner_api: Any, settings: Any
    ) -> None:
        settings.META_APP_ID = ""
        cap = owner_api.get(URL).json()["capabilities"]["embedded_signup"]
        assert cap["state"] == "unsupported" and "administrator" in cap["detail"]


class TestTemplatesAndTestMessageEvidence:
    def test_templates_need_an_approved_one(self, owner_api: Any, organization: Any) -> None:
        waba, _ = verified_account(organization)
        assert step(owner_api.get(URL).json(), "templates")["state"] == "pending"
        factories.make_template(waba)
        assert step(owner_api.get(URL).json(), "templates")["state"] == "verified"
        factories.make_template(waba, name="pending_one", status="PENDING")
        assert step(owner_api.get(URL).json(), "templates")["evidence"]["approved"] == 1

    def _test_message(self, phone: Any, status: str) -> Message:
        contact = factories.make_contact(phone.business, "919876543210")
        from apps.conversations.services import get_or_create_conversation

        conversation, _ = get_or_create_conversation(phone, contact)
        now = timezone.now()
        return Message.objects.create(
            organization=phone.organization,
            conversation=conversation,
            phone_number=phone,
            contact=contact,
            direction="outbound",
            message_type="template",
            status=status,
            idempotency_key=f"testmsg:{status}{now.timestamp()}",
            occurred_at=now,
            status_changed_at=now,
        )

    def test_the_test_message_step_follows_real_message_status(
        self, owner_api: Any, organization: Any
    ) -> None:
        _, phone = verified_account(organization)
        s = step(owner_api.get(URL).json(), "test_message")
        assert s["state"] == "pending" and s["action"] == "test_message"

        message = self._test_message(phone, "accepted")
        s = step(owner_api.get(URL).json(), "test_message")
        assert s["state"] == "pending" and "not reported it delivered" in s["detail"]

        Message.objects.filter(pk=message.pk).update(status="sent")
        assert step(owner_api.get(URL).json(), "test_message")["state"] == "pending"

        Message.objects.filter(pk=message.pk).update(status="delivered")
        assert step(owner_api.get(URL).json(), "test_message")["state"] == "verified"

    def test_a_failed_test_message_is_failed_and_points_to_failed_messages(
        self, owner_api: Any, organization: Any
    ) -> None:
        _, phone = verified_account(organization)
        self._test_message(phone, "failed")
        s = step(owner_api.get(URL).json(), "test_message")
        assert s["state"] == "failed" and "Failed messages" in s["detail"]

    def test_ordinary_messages_are_not_counted_as_test_messages(
        self, owner_api: Any, organization: Any
    ) -> None:
        _, phone = verified_account(organization)
        message = self._test_message(phone, "delivered")
        Message.objects.filter(pk=message.pk).update(idempotency_key="client-key-0001")
        assert step(owner_api.get(URL).json(), "test_message")["state"] == "pending"

    def test_ready_only_when_every_required_step_is_verified(
        self, owner_api: Any, organization: Any
    ) -> None:
        waba, phone = verified_account(organization)
        factories.make_template(waba)
        assert owner_api.get(URL).json()["overall"] == "in_progress"
        self._test_message(phone, "delivered")
        body = owner_api.get(URL).json()
        assert body["overall"] == "ready" and body["next_step"] == ""


class TestEmbeddedSignupState:
    """OAuth-style `state`: server-issued, bound, single-use, short-lived."""

    SESSION = "/api/v1/whatsapp/embedded-signup/session/"
    COMPLETE = "/api/v1/whatsapp/embedded-signup/complete/"

    def payload(self, business: Any, state: str, code: str = "AQD_code") -> dict[str, str]:
        return {
            "business_id": str(business.pk),
            "waba_id": "4242424242",
            "phone_number_id": "5550001112",
            "code": code,
            "state": state,
        }

    def issue(self, api: Any, business: Any) -> str:
        return api.post(self.SESSION, {"business_id": str(business.pk)}, format="json").json()[
            "state"
        ]

    def test_completion_without_state_is_rejected_before_any_meta_call(
        self, owner_api: Any, organization: Any, fake_graph: FakeGraph
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        body = self.payload(kiosk, "")
        del body["state"]
        assert owner_api.post(self.COMPLETE, body, format="json").status_code == 400
        assert not fake_graph.calls

    def test_a_forged_state_is_rejected_before_any_meta_call(
        self, owner_api: Any, organization: Any, fake_graph: FakeGraph
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        response = owner_api.post(self.COMPLETE, self.payload(kiosk, "made-up"), format="json")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "SIGNUP_STATE_INVALID"
        assert not fake_graph.calls

    def test_state_is_single_use_so_a_replayed_callback_is_refused(
        self, owner_api: Any, organization: Any, fake_graph: FakeGraph
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        state = self.issue(owner_api, kiosk)
        fake_graph.on(
            "GET",
            "/oauth/access_token",
            graph_error(400, 100, "expired"),
        )
        first = owner_api.post(self.COMPLETE, self.payload(kiosk, state), format="json")
        assert first.status_code == 502  # reached Meta once (fake), which refused the code
        calls = len(fake_graph.calls)
        replay = owner_api.post(self.COMPLETE, self.payload(kiosk, state), format="json")
        assert replay.status_code == 400
        assert replay.json()["error"]["code"] == "SIGNUP_STATE_INVALID"
        assert len(fake_graph.calls) == calls  # the replay never reached Meta

    def test_state_is_bound_to_the_user_who_asked_for_it(
        self, owner_api: Any, organization: Any, fake_graph: FakeGraph
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        state = self.issue(owner_api, kiosk)
        admin = factories.make_member(organization, "admin")
        response = api_client_for(admin, organization).post(
            self.COMPLETE, self.payload(kiosk, state), format="json"
        )
        assert response.status_code == 400 and not fake_graph.calls

    def test_state_is_bound_to_the_business_and_organization(
        self, owner_api: Any, organization: Any, fake_graph: FakeGraph
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        state = self.issue(owner_api, kiosk)
        assert (
            owner_api.post(self.COMPLETE, self.payload(eps, state), format="json").status_code
            == 400
        )
        assert not fake_graph.calls

    def test_state_expires(self, owner_api: Any, organization: Any, fake_graph: FakeGraph) -> None:
        from django.core.cache import cache

        kiosk = factories.make_business(organization, "Kiosk")
        state = self.issue(owner_api, kiosk)
        cache.clear()  # what TTL expiry does
        assert (
            owner_api.post(self.COMPLETE, self.payload(kiosk, state), format="json").status_code
            == 400
        )
        assert not fake_graph.calls

    def test_a_session_cannot_be_issued_for_a_business_the_user_cannot_reach(
        self, organization: Any
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        manager = factories.make_member(organization, "admin", businesses=[kiosk])
        client = api_client_for(manager, organization)
        assert (
            client.post(self.SESSION, {"business_id": str(eps.pk)}, format="json").status_code
            == 404
        )

    def test_an_agent_cannot_start_a_session(self, organization: Any) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        agent = factories.make_member(organization, "agent")
        assert (
            api_client_for(agent, organization)
            .post(self.SESSION, {"business_id": str(kiosk.pk)}, format="json")
            .status_code
            == 403
        )

    def test_the_stored_token_is_never_returned_or_audited(
        self, owner_api: Any, organization: Any, fake_graph: FakeGraph
    ) -> None:
        from apps.audit.models import AuditLog
        from tests.integration.test_domain_services import _connect_routes

        _connect_routes(fake_graph, waba="4242424242")
        fake_graph.on(
            "GET", "/oauth/access_token", {"access_token": "business-token-abcdefghijklmnop"}
        )
        fake_graph.on("POST", "/5550001112/register", {"success": True})
        kiosk = factories.make_business(organization, "Kiosk")
        state = self.issue(owner_api, kiosk)
        response = owner_api.post(self.COMPLETE, self.payload(kiosk, state), format="json")
        assert response.status_code == 202
        everything = json.dumps(response.json()) + json.dumps(
            list(AuditLog.objects.values_list("metadata", flat=True)), default=str
        )
        assert "business-token-abcdefghijklmnop" not in everything
        assert state not in everything
