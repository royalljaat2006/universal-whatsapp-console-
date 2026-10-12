"""Guided failure recovery: explanations, safe-retry rules, the retry endpoint
(tenant/business scope, permissions, idempotency, duplicate protection) and the
audit trail. The Graph API is the loud-failing fake: nothing here can reach Meta."""

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.apikeys.services import create_api_key
from apps.audit.models import AuditLog
from apps.billing.models import PricingMarket, PricingRule
from apps.contacts.services import ContactIdentifiers, resolve_contact
from apps.conversations.services import get_or_create_conversation
from apps.messaging.models import Message
from apps.messaging.services import failure_categories as fc
from apps.messaging.services import recovery
from apps.messaging.services.status import apply_status_update
from apps.messaging.state import MessageStatus
from providers.whatsapp.types import ContactProfile, ProviderErrorDetail, StatusUpdateEvent
from tests import factories
from tests.conftest import api_client_for
from tests.fakes import FakeGraph

pytestmark = pytest.mark.django_db(transaction=True)

TO = "919876543210"
ACCEPTED = {
    "messaging_product": "whatsapp",
    "contacts": [{"input": TO, "wa_id": TO}],
    "messages": [{"id": "wamid.RETRY1", "message_status": "accepted"}],
}
FAILURES = "/api/v1/messages/failures/"


def _failed(
    phone: Any,
    *,
    code: str = "PROVIDER_TRANSIENT",
    details: dict[str, Any] | None = None,
    text: str = "Your order has shipped",
    to: str = TO,
    message_type: str = "text",
    content: dict[str, Any] | None = None,
    template: Any = None,
    age: timedelta = timedelta(minutes=5),
    **extra: Any,
) -> Message:
    contact = resolve_contact(phone.business, ContactIdentifiers(phone_number=to))
    conversation, _ = get_or_create_conversation(phone, contact)
    when = timezone.now() - age
    return Message.objects.create(
        organization=phone.organization,
        conversation=conversation,
        phone_number=phone,
        contact=contact,
        direction="outbound",
        message_type=message_type,
        status=MessageStatus.FAILED,
        text=text,
        content=content if content is not None else {"body": text, "preview_url": False},
        template=template,
        occurred_at=when,
        status_changed_at=when,
        failed_at=when,
        error_code=code,
        error_message="Provider said no",
        error_details=details or {},
        **extra,
    )


def _open_window(phone: Any) -> None:
    factories.open_service_window(phone, TO)


# ---------------------------------------------------------------- the rules per category

CASES = [
    # code, details, category, kind, retryable
    ("131026", {}, fc.INVALID_RECIPIENT, "not_retryable", False),
    ("INVALID_RECIPIENT", {}, fc.INVALID_RECIPIENT, "not_retryable", False),
    ("131051", {}, fc.PROVIDER_REJECTION, "not_retryable", False),
    ("PROVIDER_REJECTED", {}, fc.PROVIDER_REJECTION, "not_retryable", False),
    ("131056", {}, fc.RATE_LIMIT, "retryable", True),
    ("PROVIDER_RATE_LIMITED", {}, fc.RATE_LIMIT, "retryable", True),
    ("190", {}, fc.AUTHENTICATION, "retryable", True),
    ("PROVIDER_AUTH_FAILED", {}, fc.AUTHENTICATION, "retryable", True),
    ("132001", {}, fc.TEMPLATE_ERROR, "not_retryable", False),
    ("TEMPLATE_NOT_SENDABLE", {}, fc.TEMPLATE_ERROR, "not_retryable", False),
    ("131047", {}, fc.POLICY_WINDOW, "not_retryable", False),
    ("SERVICE_WINDOW_CLOSED", {}, fc.POLICY_WINDOW, "not_retryable", False),
    ("PROVIDER_OUTCOME_UNKNOWN", {}, fc.TIMEOUT, "unknown", False),
    ("PROVIDER_OUTCOME_UNKNOWN", {"provider_code": "131000"}, fc.TIMEOUT, "unknown", False),
    ("131000", {}, fc.TEMPORARY, "retryable", True),
    ("PROVIDER_TRANSIENT", {}, fc.TEMPORARY, "retryable", True),
    ("CAMPAIGN_CANCELLED", {}, fc.CANCELLED, "not_retryable", False),
    ("SOMETHING_NEW", {}, fc.UNKNOWN, "unknown", False),
    ("", {}, fc.UNKNOWN, "unknown", False),
    # Only Meta can fix these: no retry, an official destination instead.
    ("131048", {}, fc.POLICY_WINDOW, "external", False),
    ("368", {}, fc.POLICY_WINDOW, "external", False),
    ("132007", {}, fc.TEMPLATE_ERROR, "external", False),
    ("132015", {}, fc.TEMPLATE_ERROR, "external", False),
    ("132016", {}, fc.TEMPLATE_ERROR, "external", False),
]


class TestAssessment:
    @pytest.mark.parametrize(("code", "details", "category", "kind", "retryable"), CASES)
    def test_every_category_gets_an_honest_verdict(
        self, phone: Any, code: str, details: dict, category: str, kind: str, retryable: bool
    ) -> None:
        info = recovery.assess(_failed(phone, code=code, details=details))
        assert (info.category, info.kind) == (category, kind)
        assert info.can_retry is retryable
        # the console never diverges from the authoritative category list
        assert (kind == "retryable") == (category in fc.RETRYABLE and kind != "external")
        assert info.explanation and info.next_step
        if not retryable:
            assert any(b.code == "not_retryable" for b in info.blockers)

    def test_meta_only_failures_point_to_official_destinations_not_a_fake_fix(
        self, phone: Any
    ) -> None:
        info = recovery.assess(_failed(phone, code="131048"))
        urls = {link["url"] for link in info.meta_links}
        assert recovery.WHATSAPP_MANAGER_URL in urls and recovery.ERROR_CODES_URL in urls
        assert "cannot be fixed from this console" in info.next_step
        assert all(u.startswith("https://") for u in urls)

    def test_console_fixable_failures_do_not_send_people_to_meta(self, phone: Any) -> None:
        urls = {link["url"] for link in recovery.assess(_failed(phone, code="131056")).meta_links}
        assert recovery.WHATSAPP_MANAGER_URL not in urls

    def test_the_provider_error_is_trimmed_and_token_like_text_is_hidden(self, phone: Any) -> None:
        secret = "EAAG" + "x" * 80
        message = _failed(phone)
        message.error_message = f"Invalid token {secret} " + "pad " * 200
        info = recovery.assess(message)
        assert secret not in info.safe_error and "[hidden]" in info.safe_error
        assert len(info.safe_error) <= 300


class TestBlockers:
    def codes(self, message: Message, **kw: Any) -> set[str]:
        return {b.code for b in recovery.assess(message, **kw).blockers}

    def test_a_clean_temporary_failure_can_be_retried(self, phone: Any) -> None:
        assert recovery.assess(_failed(phone)).can_retry is True

    def test_a_message_that_has_since_been_delivered_is_not_retryable(self, phone: Any) -> None:
        message = _failed(phone)
        Message.objects.filter(pk=message.pk).update(status=MessageStatus.DELIVERED)
        message.refresh_from_db()
        assert "not_failed" in self.codes(message)

    def test_inbound_messages_are_never_retryable(self, phone: Any) -> None:
        message = _failed(phone)
        message.direction = "inbound"
        assert "not_outbound" in self.codes(message)

    def test_campaign_messages_use_the_campaign_retry(self, phone: Any) -> None:
        message = _failed(phone)
        message.campaign_id = uuid4()  # assess() only reads the link
        assert "campaign_message" in self.codes(message)

    def test_old_failures_are_not_offered(self, phone: Any) -> None:
        assert "too_old" in self.codes(_failed(phone, age=timedelta(days=8)))
        assert "too_old" not in self.codes(_failed(phone, age=timedelta(days=6)))

    def test_a_disconnected_number_blocks(self, phone: Any) -> None:
        phone.status = "disconnected"
        phone.save()
        assert "number_not_connected" in self.codes(_failed(phone))

    def test_a_later_identical_message_that_did_not_fail_blocks_a_duplicate(
        self, phone: Any
    ) -> None:
        original = _failed(phone)
        later = _failed(phone, code="x")
        Message.objects.filter(pk=later.pk).update(
            status=MessageStatus.DELIVERED, occurred_at=timezone.now()
        )
        assert "already_resent" in self.codes(original)

    def test_a_later_identical_message_that_also_failed_does_not_block(self, phone: Any) -> None:
        original = _failed(phone)
        _failed(phone, code="x")
        assert "already_resent" not in self.codes(original)

    def test_a_different_text_or_person_never_blocks(self, phone: Any) -> None:
        original = _failed(phone)
        other = _failed(phone, text="something else")
        Message.objects.filter(pk=other.pk).update(status=MessageStatus.SENT)
        elsewhere = _failed(phone, to="919811111111")
        Message.objects.filter(pk=elsewhere.pk).update(status=MessageStatus.SENT)
        assert "already_resent" not in self.codes(original)


# ---------------------------------------------------------------- the list endpoint


class TestFailureList:
    def test_unauthenticated_is_refused(self) -> None:
        assert APIClient().get(FAILURES).status_code in (401, 403)

    def test_an_api_key_without_conversation_scope_is_refused(
        self, owner: Any, organization: Any
    ) -> None:
        _, raw = create_api_key(
            organization=organization, created_by=owner, name="k", scopes=["contacts.view"]
        )
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw}", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        assert client.get(FAILURES).status_code == 403

    def test_rows_carry_category_time_summary_and_one_next_step(
        self, owner_api: Any, phone: Any
    ) -> None:
        message = _failed(phone, code="131056", details={"provider_code": "131056"})
        body = owner_api.get(FAILURES).json()
        assert body["total"] == 1 and body["kinds"] == {"retryable": 1}
        row = body["results"][0]
        assert row["id"] == str(message.pk)
        assert row["failed_at"] and row["preview"] == "Your order has shipped"
        info = row["recovery"]
        assert info["category"] == fc.RATE_LIMIT and info["label"] == "Rate limit"
        assert info["provider_code"] == "131056" and info["safe_error"] == "Provider said no"
        assert info["can_retry"] is True and info["next_step"]
        assert row["recipient"]["phone_number"] == TO

    def test_only_recent_failed_outbound_messages_appear(self, owner_api: Any, phone: Any) -> None:
        _failed(phone)
        old = _failed(phone, text="old one", age=timedelta(days=20))
        delivered = _failed(phone, text="fine")
        Message.objects.filter(pk=delivered.pk).update(status=MessageStatus.DELIVERED)
        body = owner_api.get(FAILURES, {"days": 7}).json()
        assert body["total"] == 1
        assert str(old.pk) not in json.dumps(body) and str(delivered.pk) not in json.dumps(body)
        assert owner_api.get(FAILURES, {"days": 30}).json()["total"] == 2

    def test_bad_query_values_are_rejected(self, owner_api: Any) -> None:
        assert owner_api.get(FAILURES, {"days": "x"}).status_code == 400

    def test_another_organizations_failures_are_invisible(self, owner_api: Any, phone: Any) -> None:
        other_org = factories.make_organization()
        theirs = _failed(factories.make_phone_number(factories.make_waba(other_org)))
        mine = _failed(phone)
        body = owner_api.get(FAILURES).json()
        assert [r["id"] for r in body["results"]] == [str(mine.pk)]
        assert str(theirs.pk) not in json.dumps(body)

    def test_a_member_limited_to_one_business_sees_only_that_business(
        self, organization: Any
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        kiosk_phone = factories.make_phone_number(factories.make_waba(organization, business=kiosk))
        eps_phone = factories.make_phone_number(factories.make_waba(organization, business=eps))
        mine = _failed(kiosk_phone)
        theirs = _failed(eps_phone, to="919822222222")
        agent = factories.make_member(organization, "agent", businesses=[kiosk])
        body = api_client_for(agent, organization).get(FAILURES).json()
        assert [r["id"] for r in body["results"]] == [str(mine.pk)]
        assert str(theirs.pk) not in json.dumps(body)

    def test_the_selected_business_narrows_an_owners_view(
        self, owner: Any, organization: Any
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        _failed(factories.make_phone_number(factories.make_waba(organization, business=kiosk)))
        theirs = _failed(
            factories.make_phone_number(factories.make_waba(organization, business=eps)),
            to="919833333333",
        )
        body = api_client_for(owner, organization, eps).get(FAILURES).json()
        assert [r["id"] for r in body["results"]] == [str(theirs.pk)]

    def test_no_tokens_or_provider_ids_leak(self, owner_api: Any, phone: Any) -> None:
        _failed(phone)
        raw = json.dumps(owner_api.get(FAILURES).json())
        assert phone.external_id not in raw
        assert "token" not in raw.lower() and "secret" not in raw.lower()


# ---------------------------------------------------------------- detail + preview


class TestRecoveryDetail:
    def test_a_retryable_failure_previews_recipient_sender_text_duplicate_risk_and_cost(
        self, owner_api: Any, phone: Any
    ) -> None:
        message = _failed(phone)
        body = owner_api.get(f"/api/v1/messages/{message.pk}/recovery/").json()
        preview = body["retry_preview"]
        assert preview["confirmation_required"] is True
        assert preview["destination"]["phone_number"] == TO
        assert preview["destination"]["from_number"] == phone.display_phone_number
        assert preview["message_text"] == "Your order has shipped"
        assert preview["duplicate_risk"]["level"] == "low"
        assert preview["cost"]["status"] == "free"

    def test_no_retry_preview_when_a_retry_is_not_allowed(self, owner_api: Any, phone: Any) -> None:
        for code in ("131026", "PROVIDER_OUTCOME_UNKNOWN", "WHO_KNOWS", "131048"):
            message = _failed(phone, code=code, text=f"m-{code}")
            body = owner_api.get(f"/api/v1/messages/{message.pk}/recovery/").json()
            assert body["retry_preview"] is None and body["recovery"]["can_retry"] is False

    def test_a_template_retry_shows_a_labelled_estimate_never_a_fake_actual(
        self, owner_api: Any, phone: Any
    ) -> None:
        template = factories.make_template(phone.business_account)
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
        message = _failed(
            phone,
            message_type="template",
            template=template,
            content={
                "template_id": str(template.pk),
                "name": template.name,
                "category": "UTILITY",
                "parameters": {"header": {}, "body": {"1": "A1"}, "buttons": {}},
            },
        )
        cost = owner_api.get(f"/api/v1/messages/{message.pk}/recovery/").json()["retry_preview"][
            "cost"
        ]
        assert cost["status"] == "estimate" and Decimal(cost["amount"]) == Decimal("0.115")
        assert cost["currency"] == "INR" and "estimate" in cost["note"].lower()
        assert "only if it is delivered" in cost["note"]

    def test_a_template_without_a_loaded_rate_says_unknown(
        self, owner_api: Any, phone: Any
    ) -> None:
        template = factories.make_template(phone.business_account)
        message = _failed(
            phone,
            message_type="template",
            template=template,
            content={"template_id": str(template.pk), "category": "UTILITY", "parameters": {}},
        )
        cost = owner_api.get(f"/api/v1/messages/{message.pk}/recovery/").json()["retry_preview"][
            "cost"
        ]
        assert cost["status"] == "unknown" and "amount" not in cost

    def test_foreign_and_out_of_business_messages_are_404(
        self, owner_api: Any, organization: Any
    ) -> None:
        theirs = _failed(
            factories.make_phone_number(factories.make_waba(factories.make_organization()))
        )
        assert owner_api.get(f"/api/v1/messages/{theirs.pk}/recovery/").status_code == 404
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        other = _failed(
            factories.make_phone_number(factories.make_waba(organization, business=eps))
        )
        agent = factories.make_member(organization, "agent", businesses=[kiosk])
        assert (
            api_client_for(agent, organization).get(f"/api/v1/messages/{other.pk}/recovery/")
        ).status_code == 404


# ---------------------------------------------------------------- the retry endpoint


def _retry(api: Any, message: Message, **body: Any) -> Any:
    return api.post(
        f"/api/v1/messages/{message.pk}/retry/", body or {"confirm": True}, format="json"
    )


class TestRetry:
    def test_confirmation_is_required_and_nothing_is_sent_without_it(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        message = _failed(phone)
        assert _retry(owner_api, message, confirm=False).status_code == 400
        assert (
            owner_api.post(f"/api/v1/messages/{message.pk}/retry/", {}, format="json").status_code
            == 400
        )
        assert Message.objects.count() == 1 and not fake_graph.calls

    def test_a_confirmed_retry_creates_one_message_and_sends_it_once(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        _open_window(phone)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        message = _failed(phone)

        response = _retry(owner_api, message)

        assert response.status_code == 202, response.content
        body = response.json()
        retry = Message.objects.get(pk=body["message"]["id"])
        assert retry.pk != message.pk and retry.idempotency_key == f"retry:{message.pk}"
        assert retry.conversation_id == message.conversation_id and retry.text == message.text
        assert retry.sent_by is not None
        [sent] = fake_graph.json_bodies("POST", "/messages")
        assert sent["to"] == TO and sent["text"]["body"] == "Your order has shipped"
        # An accepted request is NOT a delivery claim.
        assert body["message"]["status"] in {"queued", "accepted"}
        assert body["message"]["delivered_at"] is None
        retry.refresh_from_db()
        assert retry.status == MessageStatus.ACCEPTED and retry.delivered_at is None
        assert "only means it was accepted" in body["status_note"]
        # the original stays what it was
        message.refresh_from_db()
        assert message.status == MessageStatus.FAILED

    def test_a_second_retry_of_the_same_message_is_refused_and_sends_nothing(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        _open_window(phone)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        message = _failed(phone)
        assert _retry(owner_api, message).status_code == 202

        again = _retry(owner_api, message)

        assert again.status_code == 409
        error = again.json()["error"]
        assert error["code"] == "RETRY_NOT_ALLOWED"
        assert error["details"]["blockers"][0]["code"] == "already_retried"
        assert Message.objects.count() == 2 and len(fake_graph.calls) == 1

    def test_the_database_itself_refuses_a_duplicate_retry(
        self, organization: Any, phone: Any, owner: Any
    ) -> None:
        from apps.messaging.services.outbound import enqueue_outbound_message

        _open_window(phone)
        message = _failed(phone)
        request = recovery.build_retry_request(message)
        first, created = enqueue_outbound_message(
            organization=organization, request=request, actor=owner
        )
        second, created_again = enqueue_outbound_message(
            organization=organization, request=request, actor=owner
        )
        assert created and not created_again and first.pk == second.pk
        assert Message.objects.filter(idempotency_key=f"retry:{message.pk}").count() == 1

    @pytest.mark.parametrize("code", ["131026", "PROVIDER_OUTCOME_UNKNOWN", "WHO_KNOWS", "131048"])
    def test_non_retryable_unknown_timeout_and_external_failures_are_never_retried(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph, code: str
    ) -> None:
        message = _failed(phone, code=code)
        response = _retry(owner_api, message)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RETRY_NOT_ALLOWED"
        assert Message.objects.count() == 1 and not fake_graph.calls
        refused = AuditLog.objects.get(action="message.retry_refused")
        assert refused.metadata["blockers"] and refused.resource_id == str(message.pk)

    def test_a_delivered_message_cannot_be_retried(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        message = _failed(phone)
        Message.objects.filter(pk=message.pk).update(status=MessageStatus.DELIVERED)
        assert _retry(owner_api, message).status_code == 409
        assert not fake_graph.calls

    def test_a_viewer_cannot_retry_and_an_agent_can(
        self, organization: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        _open_window(phone)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        message = _failed(phone)
        viewer = factories.make_member(organization, "viewer")
        assert _retry(api_client_for(viewer, organization), message).status_code == 403
        assert not fake_graph.calls
        agent = factories.make_member(organization, "agent")
        assert _retry(api_client_for(agent, organization), message).status_code == 202

    def test_an_api_key_without_send_scope_is_refused(
        self, owner: Any, organization: Any, phone: Any
    ) -> None:
        message = _failed(phone)
        _, raw = create_api_key(
            organization=organization, created_by=owner, name="k", scopes=["conversations.view"]
        )
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw}", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        assert _retry(client, message).status_code == 403

    def test_foreign_organization_and_other_business_are_404_and_send_nothing(
        self, owner_api: Any, organization: Any, fake_graph: FakeGraph
    ) -> None:
        theirs = _failed(
            factories.make_phone_number(factories.make_waba(factories.make_organization()))
        )
        assert _retry(owner_api, theirs).status_code == 404

        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        other = _failed(
            factories.make_phone_number(factories.make_waba(organization, business=eps)),
            to="919844444444",
        )
        agent = factories.make_member(organization, "agent", businesses=[kiosk])
        assert _retry(api_client_for(agent, organization), other).status_code == 404
        assert not fake_graph.calls
        assert not Message.objects.filter(idempotency_key__startswith="retry:").exists()

    def test_a_retry_into_a_closed_service_window_is_refused_by_the_send_rules(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        message = _failed(phone)  # no inbound from the customer: window closed
        response = _retry(owner_api, message)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SERVICE_WINDOW_CLOSED"
        assert not fake_graph.calls
        # nothing was recorded as retried, so the person can still act
        assert not Message.objects.filter(idempotency_key=f"retry:{message.pk}").exists()

    def test_a_template_retry_reuses_the_original_parameters(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        template = factories.make_template(phone.business_account)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        message = _failed(
            phone,
            message_type="template",
            template=template,
            text="Order A1 shipped",
            content={
                "template_id": str(template.pk),
                "name": template.name,
                "category": "UTILITY",
                "parameters": {"header": {}, "body": {"1": "A1"}, "buttons": {}},
            },
        )
        assert _retry(owner_api, message).status_code == 202
        [sent] = fake_graph.json_bodies("POST", "/messages")
        assert sent["template"]["name"] == template.name
        assert sent["template"]["components"][0]["parameters"][0]["text"] == "A1"

    def test_a_retry_that_fails_again_is_reported_as_failed_and_can_be_retried_in_turn(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        _open_window(phone)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        original = _failed(phone)
        response = _retry(owner_api, original)
        assert response.status_code == 202
        retry = Message.objects.get(pk=response.json()["message"]["id"])
        _report(
            phone,
            "wamid.RETRY1",
            "failed",
            (ProviderErrorDetail(code="131056", message="pair rate limit"),),
        )

        # The failure is surfaced truthfully through the same recovery API.
        retry.refresh_from_db()
        assert retry.status == MessageStatus.FAILED
        listing = owner_api.get(FAILURES).json()
        original_row = next(r for r in listing["results"] if r["id"] == str(original.pk))
        replacement = original_row["recovery"]["retried_as"]
        assert replacement["status"] == "failed"
        assert replacement["failure"]["category"] == fc.RATE_LIMIT
        assert original_row["recovery"]["can_retry"] is False

        # The new failure is its own message with its own (single) retry.
        assert recovery.assess(retry).can_retry is True


# ---------------------------------------------------------------- truthful status + audit


def _report(phone: Any, wamid: str, status: str, errors: tuple = ()) -> None:
    apply_status_update(
        StatusUpdateEvent(
            phone_number_external_id=phone.external_id,
            business_account_external_id=phone.business_account.external_id,
            external_message_id=wamid,
            status=status,
            occurred_at=timezone.now(),
            recipient=ContactProfile(phone_number=TO, bsuid=None),
            errors=errors,
        ),
        provider="meta",
    )


class TestTruthfulStatusAndAudit:
    def _retried(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> tuple[Message, Message]:
        _open_window(phone)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        original = _failed(phone)
        response = _retry(owner_api, original)
        return original, Message.objects.get(pk=response.json()["message"]["id"])

    def test_delivery_is_reported_only_when_the_provider_says_so(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        original, _ = self._retried(owner_api, phone, fake_graph)

        def state() -> dict:
            rows = owner_api.get(FAILURES).json()["results"]
            return next(r for r in rows if r["id"] == str(original.pk))["recovery"]["retried_as"]

        assert state()["status"] == "accepted" and state()["delivered_at"] is None
        _report(phone, "wamid.RETRY1", "sent")
        assert state()["status"] == "sent" and state()["delivered_at"] is None
        _report(phone, "wamid.RETRY1", "delivered")
        assert state()["status"] == "delivered" and state()["delivered_at"]

    def test_a_failure_reported_by_the_provider_after_acceptance_is_shown(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        original, _retry_msg = self._retried(owner_api, phone, fake_graph)
        _report(
            phone,
            "wamid.RETRY1",
            "failed",
            (ProviderErrorDetail(code="131026", message="Message undeliverable"),),
        )
        rows = owner_api.get(FAILURES).json()["results"]
        row = next(r for r in rows if r["id"] == str(original.pk))
        assert row["recovery"]["retried_as"]["status"] == "failed"
        assert row["recovery"]["retried_as"]["failure"]["category"] == fc.INVALID_RECIPIENT

    def test_the_request_and_each_outcome_are_audited_without_sensitive_data(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        original, retry = self._retried(owner_api, phone, fake_graph)
        requested = AuditLog.objects.get(action="message.retry_requested")
        assert requested.metadata["retry_message_id"] == str(retry.pk)
        assert requested.resource_id == str(original.pk) and requested.actor is not None

        _report(phone, "wamid.RETRY1", "delivered")
        _report(phone, "wamid.RETRY1", "read")
        outcomes = AuditLog.objects.filter(action="message.retry_outcome")
        assert outcomes.count() == 1
        outcome = outcomes.get()
        assert outcome.metadata["outcome"] == "delivered"
        assert outcome.metadata["original_message_id"] == str(original.pk)

        everything = json.dumps(
            list(AuditLog.objects.values_list("metadata", flat=True)), default=str
        )
        assert TO not in everything and "Your order has shipped" not in everything

    def test_a_failed_outcome_is_audited_with_its_category(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        self._retried(owner_api, phone, fake_graph)
        _report(
            phone,
            "wamid.RETRY1",
            "failed",
            (ProviderErrorDetail(code="131026", message="x"),),
        )
        outcome = AuditLog.objects.get(action="message.retry_outcome")
        assert outcome.metadata["outcome"] == "failed"
        assert outcome.metadata["category"] == fc.INVALID_RECIPIENT

    def test_ordinary_messages_do_not_write_retry_outcomes(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        _open_window(phone)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        owner_api.post(
            "/api/v1/messages/",
            {"phone_number_id": str(phone.pk), "to": TO, "type": "text", "text": {"body": "hi"}},
            format="json",
        )
        _report(phone, "wamid.RETRY1", "delivered")
        assert not AuditLog.objects.filter(action="message.retry_outcome").exists()
