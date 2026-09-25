"""Outbound pipeline: API accept -> queue -> dispatch (fake Graph) -> state."""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.messaging.models import Message
from apps.messaging.services.outbound import dispatch_outbound_message, fail_stuck_sending_messages
from apps.messaging.state import MessageStatus
from tests import factories
from tests.conftest import api_client_for
from tests.fakes import FakeGraph, graph_error, raise_exc

pytestmark = pytest.mark.django_db(transaction=True)

TO = "919876543210"
ACCEPTED = {
    "messaging_product": "whatsapp",
    "contacts": [{"input": TO, "wa_id": TO}],
    "messages": [{"id": "wamid.SENT1", "message_status": "accepted"}],
}


def text_payload(phone: Any, body: str = "hello") -> dict[str, Any]:
    return {"phone_number_id": str(phone.pk), "to": TO, "type": "text", "text": {"body": body}}


class TestSendText:
    def test_accepted_and_dispatched(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        factories.open_service_window(phone, TO)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)

        response = owner_api.post("/api/v1/messages/", text_payload(phone), format="json")

        assert response.status_code == 202, response.content
        message = Message.objects.get(pk=response.json()["id"])
        assert message.status == MessageStatus.ACCEPTED
        assert message.external_id == "wamid.SENT1"
        assert message.send_attempts == 1
        [body] = fake_graph.json_bodies("POST", "/messages")
        assert body["to"] == TO and body["text"]["body"] == "hello"
        assert AuditLog.objects.filter(action="message.sent_manually").count() == 1

    def test_service_window_closed_rejects_free_form(self, owner_api: Any, phone: Any) -> None:
        response = owner_api.post("/api/v1/messages/", text_payload(phone), format="json")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SERVICE_WINDOW_CLOSED"
        assert Message.objects.count() == 0

    def test_template_requires_approved_template(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        pending = factories.make_template(phone.business_account, status="PENDING")
        payload = {
            "phone_number_id": str(phone.pk),
            "to": TO,
            "type": "template",
            "template": {"template_id": str(pending.pk), "parameters": {"body": {"1": "A1"}}},
        }
        response = owner_api.post("/api/v1/messages/", payload, format="json")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "TEMPLATE_NOT_SENDABLE"

        approved = factories.make_template(phone.business_account, name="approved_one")
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        payload["template"]["template_id"] = str(approved.pk)
        response = owner_api.post("/api/v1/messages/", payload, format="json")
        assert response.status_code == 202, response.content
        [body] = fake_graph.json_bodies("POST", "/messages")
        assert body["template"] == {
            "name": "approved_one",
            "language": {"code": "en"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": "A1"}]}],
        }

    def test_invalid_phone_is_validation_error(self, owner_api: Any, phone: Any) -> None:
        payload = text_payload(phone)
        payload["to"] = "abc"
        response = owner_api.post("/api/v1/messages/", payload, format="json")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_disconnected_number_cannot_send(self, owner_api: Any, waba: Any) -> None:
        phone = factories.make_phone_number(waba, status="disconnected")
        response = owner_api.post("/api/v1/messages/", text_payload(phone), format="json")
        assert response.json()["error"]["code"] == "PHONE_NUMBER_NOT_READY"


class TestIdempotency:
    def test_same_key_same_payload_replays(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        factories.open_service_window(phone, TO)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        headers = {"HTTP_IDEMPOTENCY_KEY": "client-key-0001"}

        first = owner_api.post("/api/v1/messages/", text_payload(phone), format="json", **headers)
        second = owner_api.post("/api/v1/messages/", text_payload(phone), format="json", **headers)

        assert first.status_code == 202
        assert second.status_code == 200
        assert second["Idempotent-Replayed"] == "true"
        assert first.json()["id"] == second.json()["id"]
        assert Message.objects.count() == 1
        assert len(fake_graph.calls) == 1  # provider called once

    def test_same_key_different_payload_conflicts(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        factories.open_service_window(phone, TO)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        headers = {"HTTP_IDEMPOTENCY_KEY": "client-key-0002"}
        owner_api.post("/api/v1/messages/", text_payload(phone, "a"), format="json", **headers)
        response = owner_api.post(
            "/api/v1/messages/", text_payload(phone, "b"), format="json", **headers
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    def test_dispatch_is_noop_unless_queued(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        factories.open_service_window(phone, TO)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        message_id = owner_api.post("/api/v1/messages/", text_payload(phone), format="json").json()[
            "id"
        ]
        # A redelivered task must not send again.
        assert dispatch_outbound_message(message_id).status == "skipped"
        assert len(fake_graph.calls) == 1


class TestProviderFailures:
    def _send(self, owner_api: Any, phone: Any) -> Message:
        factories.open_service_window(phone, TO)
        response = owner_api.post("/api/v1/messages/", text_payload(phone), format="json")
        return Message.objects.get(pk=response.json()["id"])

    def test_rate_limited_then_accepted(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph, monkeypatch: Any
    ) -> None:
        # Drive dispatch as a worker would (eager Celery cannot reschedule).
        monkeypatch.setattr("apps.messaging.services.outbound.enqueue_dispatch", lambda _id: None)
        fake_graph.on(
            "POST",
            f"/{phone.external_id}/messages",
            graph_error(400, 130429),
            lambda r: httpx.Response(200, json=ACCEPTED),
        )
        message = self._send(owner_api, phone)

        first = dispatch_outbound_message(message.pk)
        assert first.retry_in is not None and first.retry_in > 0
        message.refresh_from_db()
        assert message.status == MessageStatus.QUEUED  # safe to retry: request was rejected

        assert dispatch_outbound_message(message.pk).status == MessageStatus.ACCEPTED
        message.refresh_from_db()
        assert message.send_attempts == 2
        assert message.external_id == "wamid.SENT1"

    def test_permanent_error_fails_without_retry(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        fake_graph.on(
            "POST", f"/{phone.external_id}/messages", graph_error(400, 131026, "undeliverable")
        )
        message = self._send(owner_api, phone)
        assert message.status == MessageStatus.FAILED
        assert message.error_code == "PROVIDER_REJECTED"
        assert message.error_details["provider_code"] == "131026"
        assert len(fake_graph.calls) == 1

    def test_timeout_after_send_is_never_retried(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        fake_graph.on(
            "POST", f"/{phone.external_id}/messages", raise_exc(httpx.ReadTimeout("slow"))
        )
        message = self._send(owner_api, phone)
        assert message.status == MessageStatus.FAILED
        assert message.error_code == "PROVIDER_OUTCOME_UNKNOWN"
        assert len(fake_graph.calls) == 1

    def test_missing_credential_fails_as_configuration_required(
        self, owner_api: Any, phone: Any, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv(factories.TEST_TOKEN_ENV)
        message = self._send(owner_api, phone)
        assert message.status == MessageStatus.FAILED
        assert message.error_code == "CONFIGURATION_REQUIRED"


def test_stuck_sending_messages_are_failed_not_resent(
    owner_api: Any, phone: Any, fake_graph: FakeGraph
) -> None:
    factories.open_service_window(phone, TO)
    fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
    message = Message.objects.get(
        pk=owner_api.post("/api/v1/messages/", text_payload(phone), format="json").json()["id"]
    )
    Message.objects.filter(pk=message.pk).update(
        status=MessageStatus.SENDING, sending_at=timezone.now() - timedelta(hours=1)
    )
    assert fail_stuck_sending_messages() == 1
    message.refresh_from_db()
    assert message.status == MessageStatus.FAILED
    assert message.error_code == "PROVIDER_OUTCOME_UNKNOWN"


def test_viewer_cannot_send(organization: Any, phone: Any) -> None:
    viewer = factories.make_member(organization, "viewer")
    response = api_client_for(viewer, organization).post(
        "/api/v1/messages/", text_payload(phone), format="json"
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
