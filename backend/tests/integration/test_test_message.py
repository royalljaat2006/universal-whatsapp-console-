"""Verified test message: preview, separate approval gate, real send through the
normal pipeline, truthful status. The Graph API is the loud-failing fake: any
send that is not explicitly routed fails the test, so nothing here can reach Meta."""

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.billing.models import PricingMarket, PricingRule
from apps.messaging.models import Message
from apps.messaging.services.status import apply_status_update
from apps.templates.models import MessageTemplate
from providers.whatsapp.types import ContactProfile, ProviderErrorDetail, StatusUpdateEvent
from tests import factories
from tests.conftest import api_client_for
from tests.fakes import FakeGraph

pytestmark = pytest.mark.django_db(transaction=True)

PREVIEW = "/api/v1/whatsapp/test-message/preview/"
SEND = "/api/v1/whatsapp/test-message/send/"
CANCEL = "/api/v1/whatsapp/test-message/cancel/"
SETUP = "/api/v1/whatsapp/setup/"
TO = "919876543210"
ACCEPTED = {
    "messaging_product": "whatsapp",
    "contacts": [{"input": TO, "wa_id": TO}],
    "messages": [{"id": "wamid.TEST1", "message_status": "accepted"}],
}


def hello_template(waba: Any, **over: Any) -> MessageTemplate:
    defaults: dict[str, Any] = {
        "business_account": waba,
        "external_id": f"t{MessageTemplate.objects.count() + 1}",
        "name": "hello_world",
        "language": "en_US",
        "category": "UTILITY",
        "status": "APPROVED",
        "components": [{"type": "BODY", "text": "Hello! This is a test from WhatsApp Console."}],
    }
    return MessageTemplate.objects.create(**{**defaults, **over})


@pytest.fixture
def ready(organization: Any, waba: Any, phone: Any) -> dict[str, Any]:
    template = hello_template(waba)
    contact = factories.make_contact(phone.business, TO, name="Asha Rao", opt_in="opted_in")
    return {"phone": phone, "template": template, "contact": contact, "waba": waba}


def preview(api: Any, ready: dict[str, Any], **over: Any) -> Any:
    body = {"phone_number_id": str(ready["phone"].pk), "contact_id": str(ready["contact"].pk)}
    return api.post(PREVIEW, {**body, **over}, format="json")


def approved_token(api: Any, ready: dict[str, Any], **over: Any) -> str:
    response = preview(api, ready, **over)
    assert response.status_code == 200, response.content
    return response.json()["approval"]["token"]


def send(api: Any, token: str, approve: bool = True) -> Any:
    return api.post(SEND, {"approval_token": token, "approve": approve}, format="json")


class TestPreview:
    def test_a_preview_shows_everything_and_sends_nothing(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        response = preview(owner_api, ready)
        assert response.status_code == 200
        body = response.json()
        assert body["can_send"] is True and body["blockers"] == []
        assert body["sender"]["display_phone_number"] == ready["phone"].display_phone_number
        assert body["recipient"] == {"name": "Asha Rao", "phone_number": TO}
        assert body["message_type"] == "template"
        assert body["content"] == "Hello! This is a test from WhatsApp Console."
        assert body["template"]["name"] == "hello_world"
        assert body["authorization"] == "opted_in_contact"
        assert body["cost"]["status"] in {"estimate", "unknown"}
        assert body["restrictions"] and all(r["text"] for r in body["restrictions"])
        assert body["approval"]["token"] and body["approval"]["expires_in"] > 0
        assert not fake_graph.calls and Message.objects.count() == 0

    def test_a_number_that_is_not_a_contact_needs_an_explicit_attestation(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        blocked = owner_api.post(
            PREVIEW,
            {"phone_number_id": str(ready["phone"].pk), "to": "919811111111"},
            format="json",
        )
        body = blocked.json()
        assert body["can_send"] is False and body["approval"] is None
        assert body["blockers"][0]["code"] == "consent_required"

        attested = owner_api.post(
            PREVIEW,
            {
                "phone_number_id": str(ready["phone"].pk),
                "to": "919811111111",
                "attest_consent": True,
            },
            format="json",
        ).json()
        assert attested["can_send"] is True and attested["authorization"] == "attested"
        assert not fake_graph.calls

    def test_an_opted_out_contact_is_refused_even_with_attestation(
        self, owner_api: Any, ready: dict[str, Any]
    ) -> None:
        contact = ready["contact"]
        contact.opt_in_status = "opted_out"
        contact.save()
        body = owner_api.post(
            PREVIEW,
            {
                "phone_number_id": str(ready["phone"].pk),
                "to": TO,
                "attest_consent": True,
            },
            format="json",
        ).json()
        assert body["can_send"] is False
        assert any(b["code"] == "opted_out" for b in body["blockers"])
        assert body["approval"] is None

    def test_an_invalid_number_is_a_plain_blocker(
        self, owner_api: Any, ready: dict[str, Any]
    ) -> None:
        body = owner_api.post(
            PREVIEW,
            {"phone_number_id": str(ready["phone"].pk), "to": "abc", "attest_consent": True},
            format="json",
        ).json()
        assert body["can_send"] is False and body["blockers"][0]["code"] == "invalid_number"

    def test_content_comes_only_from_an_approved_variable_free_template(
        self, owner_api: Any, ready: dict[str, Any], waba: Any
    ) -> None:
        ready["template"].status = "PENDING"
        ready["template"].save()
        hello_template(
            waba, name="needs_variable", components=[{"type": "BODY", "text": "Hi {{1}}"}]
        )
        body = preview(owner_api, ready).json()
        assert body["can_send"] is False
        assert body["blockers"][0]["code"] == "no_template" and body["approval"] is None

    def test_free_text_cannot_be_supplied_by_the_browser(
        self, owner_api: Any, ready: dict[str, Any]
    ) -> None:
        body = preview(owner_api, ready, text="Buy my crypto!", content="Buy my crypto!").json()
        assert body["content"] == "Hello! This is a test from WhatsApp Console."

    def test_a_template_with_variables_cannot_be_chosen(
        self, owner_api: Any, ready: dict[str, Any], waba: Any
    ) -> None:
        other = hello_template(
            waba, name="needs_variable", components=[{"type": "BODY", "text": "Hi {{1}}"}]
        )
        body = preview(owner_api, ready, template_id=str(other.pk)).json()
        assert body["can_send"] is False and body["blockers"][0]["code"] == "template_unavailable"

    def test_a_disconnected_number_blocks(self, owner_api: Any, ready: dict[str, Any]) -> None:
        ready["phone"].status = "disconnected"
        ready["phone"].save()
        body = preview(owner_api, ready).json()
        assert body["can_send"] is False
        assert any(b["code"] == "number_not_connected" for b in body["blockers"])

    def test_cost_is_a_labelled_estimate_or_unknown_never_invented(
        self, owner_api: Any, ready: dict[str, Any]
    ) -> None:
        assert preview(owner_api, ready).json()["cost"]["status"] == "unknown"
        PricingMarket.objects.create(
            provider="meta",
            code="IN",
            name="India",
            calling_codes=["91"],
            effective_from=timezone.now() - timedelta(days=60),
            source="test rate card",
        )
        PricingRule.objects.create(
            provider="meta",
            market="IN",
            category="utility",
            currency="INR",
            rate=Decimal("0.115"),
            effective_from=timezone.now() - timedelta(days=60),
            source="test rate card",
            version="t1",
        )
        cost = preview(owner_api, ready).json()["cost"]
        assert cost["status"] == "estimate" and Decimal(cost["amount"]) == Decimal("0.115")
        assert "estimate" in cost["note"].lower() and "only if it is delivered" in cost["note"]


class TestAuthorization:
    def test_unauthenticated_is_refused(self) -> None:
        assert APIClient().post(PREVIEW, {}, format="json").status_code in (401, 403)

    def test_a_viewer_cannot_preview_or_send(
        self, organization: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        viewer = factories.make_member(organization, "viewer")
        api = api_client_for(viewer, organization)
        assert preview(api, ready).status_code == 403
        assert send(api, "whatever").status_code == 403
        assert not fake_graph.calls

    def test_a_foreign_number_is_404(self, owner_api: Any, fake_graph: FakeGraph) -> None:
        other = factories.make_organization()
        theirs = factories.make_phone_number(factories.make_waba(other))
        response = owner_api.post(
            PREVIEW,
            {"phone_number_id": str(theirs.pk), "to": TO, "attest_consent": True},
            format="json",
        )
        assert response.status_code == 404 and not fake_graph.calls

    def test_a_number_in_a_business_the_member_cannot_reach_is_404(
        self, organization: Any, fake_graph: FakeGraph
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        eps_phone = factories.make_phone_number(factories.make_waba(organization, business=eps))
        agent = factories.make_member(organization, "agent", businesses=[kiosk])
        response = api_client_for(agent, organization).post(
            PREVIEW,
            {"phone_number_id": str(eps_phone.pk), "to": TO, "attest_consent": True},
            format="json",
        )
        assert response.status_code == 404 and not fake_graph.calls

    def test_a_contact_from_another_business_is_404(
        self, owner_api: Any, organization: Any, ready: dict[str, Any]
    ) -> None:
        eps = factories.make_business(organization, "EPS")
        stranger = factories.make_contact(eps, "919822222222", opt_in="opted_in")
        response = preview(owner_api, ready, contact_id=str(stranger.pk))
        assert response.status_code == 404


class TestApprovalGate:
    def test_a_preview_alone_never_sends(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        approved_token(owner_api, ready)
        assert not fake_graph.calls and Message.objects.count() == 0

    @pytest.mark.parametrize("approve", [False, None])
    def test_sending_requires_explicit_approval(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph, approve: Any
    ) -> None:
        token = approved_token(owner_api, ready)
        body: dict[str, Any] = {"approval_token": token}
        if approve is not None:
            body["approve"] = approve
        response = owner_api.post(SEND, body, format="json")
        assert response.status_code in (400, 409)
        assert not fake_graph.calls and Message.objects.count() == 0
        # the token is still usable afterwards: refusing did not burn or send it
        assert cache.get(f"wa-testmsg-approval:{token}")

    def test_a_made_up_expired_or_other_users_token_is_refused(
        self, owner_api: Any, organization: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        assert send(owner_api, "made-up-token").status_code == 409
        token = approved_token(owner_api, ready)
        admin = factories.make_member(organization, "admin")
        other = api_client_for(admin, organization)
        response = send(other, token)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "TEST_APPROVAL_INVALID"
        cache.clear()  # TTL expiry
        assert send(owner_api, token).status_code == 409
        assert not fake_graph.calls and Message.objects.count() == 0

    def test_cancelling_discards_the_approval_and_is_audited(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        token = approved_token(owner_api, ready)
        assert owner_api.post(CANCEL, {"approval_token": token}, format="json").status_code == 200
        assert send(owner_api, token).status_code == 409
        assert AuditLog.objects.filter(action="whatsapp.test_message_cancelled").count() == 1
        assert not fake_graph.calls and Message.objects.count() == 0

    def test_cancelling_someone_elses_token_does_nothing(
        self, owner_api: Any, organization: Any, ready: dict[str, Any]
    ) -> None:
        token = approved_token(owner_api, ready)
        admin = factories.make_member(organization, "admin")
        api_client_for(admin, organization).post(CANCEL, {"approval_token": token}, format="json")
        assert cache.get(f"wa-testmsg-approval:{token}")
        assert not AuditLog.objects.filter(action="whatsapp.test_message_cancelled").exists()

    @pytest.mark.parametrize("change", ["disconnect", "unapprove", "opt_out"])
    def test_anything_that_changes_after_the_preview_refuses_the_send(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph, change: str
    ) -> None:
        token = approved_token(owner_api, ready)
        if change == "disconnect":
            ready["phone"].status = "disconnected"
            ready["phone"].save()
        elif change == "unapprove":
            ready["template"].status = "PAUSED"
            ready["template"].save()
        else:
            ready["contact"].opt_in_status = "opted_out"
            ready["contact"].save()
        response = send(owner_api, token)
        assert response.status_code == 409
        assert not fake_graph.calls and Message.objects.count() == 0


class TestSend:
    def test_an_approved_send_uses_the_pipeline_and_reports_accepted_not_delivered(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        fake_graph.on("POST", f"/{ready['phone'].external_id}/messages", ACCEPTED)
        token = approved_token(owner_api, ready)

        response = send(owner_api, token)

        assert response.status_code == 202, response.content
        body = response.json()
        message = Message.objects.get(pk=body["message_id"])
        assert message.idempotency_key.startswith("testmsg:")
        assert message.message_type == "template" and message.sent_by is not None
        assert message.status == "accepted" and message.delivered_at is None
        assert "does not mean it was delivered" in body["status_note"]
        [sent] = fake_graph.json_bodies("POST", "/messages")
        assert sent["to"] == TO and sent["template"]["name"] == "hello_world"
        assert sent["template"]["language"] == {"code": "en_US"}
        assert AuditLog.objects.filter(action="whatsapp.test_message_approved").count() == 1
        assert AuditLog.objects.filter(action="message.sent_manually").count() == 1
        raw = json.dumps(list(AuditLog.objects.values_list("metadata", flat=True)), default=str)
        assert token not in raw and TO not in raw

    def test_the_approval_is_single_use(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        fake_graph.on("POST", f"/{ready['phone'].external_id}/messages", ACCEPTED)
        token = approved_token(owner_api, ready)
        assert send(owner_api, token).status_code == 202
        assert send(owner_api, token).status_code == 409
        assert Message.objects.count() == 1 and len(fake_graph.calls) == 1

    def test_a_replayed_approval_cannot_send_twice(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        """Two near-simultaneous calls that both saw the token: the idempotency
        key derived from it still allows exactly one message."""
        fake_graph.on("POST", f"/{ready['phone'].external_id}/messages", ACCEPTED)
        token = approved_token(owner_api, ready)
        key = f"wa-testmsg-approval:{token}"
        stored = cache.get(key)
        first = send(owner_api, token)
        cache.set(key, stored, 600)  # the second request had already read it
        second = send(owner_api, token)
        assert first.status_code == 202 and second.status_code == 200
        assert first.json()["message_id"] == second.json()["message_id"]
        assert second.json()["created"] is False
        assert Message.objects.count() == 1 and len(fake_graph.calls) == 1


def _report(phone: Any, status: str, errors: tuple = ()) -> None:
    apply_status_update(
        StatusUpdateEvent(
            phone_number_external_id=phone.external_id,
            business_account_external_id=phone.business_account.external_id,
            external_message_id="wamid.TEST1",
            status=status,
            occurred_at=timezone.now(),
            recipient=ContactProfile(phone_number=TO, bsuid=None),
            errors=errors,
        ),
        provider="meta",
    )


class TestTruthfulStates:
    def sent(self, api: Any, ready: dict[str, Any], fake_graph: FakeGraph) -> Message:
        fake_graph.on("POST", f"/{ready['phone'].external_id}/messages", ACCEPTED)
        response = send(api, approved_token(api, ready))
        return Message.objects.get(pk=response.json()["message_id"])

    def setup_step(self, api: Any) -> dict[str, Any]:
        return next(s for s in api.get(SETUP).json()["steps"] if s["id"] == "test_message")

    def test_each_state_is_the_providers_not_ours(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        message = self.sent(owner_api, ready, fake_graph)
        phone = ready["phone"]

        def status() -> str:
            return owner_api.get(f"/api/v1/messages/{message.pk}/").json()["status"]

        assert status() == "accepted"
        assert self.setup_step(owner_api)["state"] == "pending"
        _report(phone, "sent")
        assert status() == "sent" and self.setup_step(owner_api)["state"] == "pending"
        _report(phone, "delivered")
        assert status() == "delivered" and self.setup_step(owner_api)["state"] == "verified"
        _report(phone, "read")
        assert status() == "read" and self.setup_step(owner_api)["state"] == "verified"

    def test_a_provider_failure_is_failed_and_shows_up_in_failed_messages(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        message = self.sent(owner_api, ready, fake_graph)
        _report(
            ready["phone"],
            "failed",
            (ProviderErrorDetail(code="131026", message="Message undeliverable"),),
        )
        assert Message.objects.get(pk=message.pk).status == "failed"
        assert self.setup_step(owner_api)["state"] == "failed"
        rows = owner_api.get("/api/v1/messages/failures/").json()["results"]
        assert [r["id"] for r in rows] == [str(message.pk)]
        assert rows[0]["recovery"]["category"] == "invalid_recipient"

    def test_a_webhook_event_makes_delivery_evidence_real(
        self, owner_api: Any, ready: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        self.sent(owner_api, ready, fake_graph)
        _report(ready["phone"], "delivered")
        # status updates applied through the pipeline are what verify the test step; the
        # webhook-delivery step itself still needs a stored, signed event (see setup tests).
        body = owner_api.get(SETUP).json()
        delivery = next(s for s in body["steps"] if s["id"] == "webhook_delivery")
        assert delivery["state"] in {"pending", "unknown"}
