"""End-to-end webhook pipeline against real PostgreSQL:
HTTP -> signature -> raw persistence -> async processing -> domain state."""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.contacts.models import Contact, ContactIdentity
from apps.conversations.models import Conversation
from apps.messaging.models import Direction, Message, MessageStatusEvent
from apps.messaging.state import MessageStatus
from apps.webhooks.models import WebhookEvent, WebhookProcessingAttempt
from apps.webhooks.processor import due_event_ids, mark_for_replay, process_event
from tests import factories
from tests.conftest import load_fixture

pytestmark = pytest.mark.django_db(transaction=True)

TS = 1_780_000_000


def ids(phone: Any, wamid: str = "wamid.IN1", ts: int = TS, **extra: str) -> dict[str, str]:
    return {
        "WABA_ID": phone.business_account.external_id,
        "PHONE_NUMBER_ID": phone.external_id,
        "WAMID": wamid,
        "TS": str(ts),
        **extra,
    }


def make_outbound(phone: Any, wamid: str, status: str = MessageStatus.ACCEPTED) -> Message:
    factories.open_service_window(phone, "16505551234")
    conversation = Conversation.objects.get(phone_number=phone)
    now = timezone.now()
    return Message.objects.create(
        organization=phone.organization,
        conversation=conversation,
        phone_number=phone,
        contact=conversation.contact,
        direction=Direction.OUTBOUND,
        message_type="text",
        status=status,
        external_id=wamid,
        content={"body": "hi"},
        occurred_at=now,
        status_changed_at=now,
    )


class TestHttpLayer:
    def test_handshake(self, client: Any) -> None:
        response = client.get(
            "/webhooks/v1/meta/whatsapp/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "42",
            },
        )
        assert response.status_code == 200
        assert response.content == b"42"
        assert response["Content-Type"].startswith("text/plain")

    def test_handshake_wrong_token(self, client: Any) -> None:
        response = client.get(
            "/webhooks/v1/meta/whatsapp/",
            {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"},
        )
        assert response.status_code == 403

    def test_invalid_signature_is_rejected_and_not_stored(
        self, post_webhook: Any, phone: Any
    ) -> None:
        response = post_webhook(
            load_fixture("inbound_text.json", **ids(phone)), signature="sha256=bad"
        )
        assert response.status_code == 401
        assert WebhookEvent.objects.count() == 0

    def test_non_json_body_rejected(self, client: Any) -> None:
        from tests.conftest import sign

        response = client.post(
            "/webhooks/v1/meta/whatsapp/",
            data=b"not json",
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sign(b"not json"),
        )
        assert response.status_code == 400

    def test_valid_delivery_is_persisted_with_routing_keys(
        self, post_webhook: Any, phone: Any
    ) -> None:
        response = post_webhook(load_fixture("inbound_text.json", **ids(phone)))
        assert response.status_code == 200
        event = WebhookEvent.objects.get()
        assert event.phone_number_external_ids == [phone.external_id]
        assert event.fields == ["messages"]
        assert event.payload["object"] == "whatsapp_business_account"
        # processed asynchronously (eager Celery in tests) after commit
        assert event.status == WebhookEvent.Status.PROCESSED
        assert event.organization_ids == [phone.organization_id]
        assert event.request_id


class TestInbound:
    def test_inbound_creates_contact_conversation_and_message(
        self, post_webhook: Any, phone: Any
    ) -> None:
        post_webhook(load_fixture("inbound_text.json", **ids(phone)))
        message = Message.objects.get()
        assert message.direction == Direction.INBOUND
        assert message.status == MessageStatus.RECEIVED
        assert message.text == "Does it come in another color?"
        assert message.organization_id == phone.organization_id
        contact = Contact.objects.get()
        assert contact.profile_name == "Sheena Nelson"
        kinds = set(ContactIdentity.objects.values_list("kind", "value"))
        assert kinds == {("phone", "16505551234"), ("bsuid", "US.13491208655302741918")}
        conversation = Conversation.objects.get()
        assert conversation.unread_count == 1
        assert conversation.is_service_window_open(now=message.occurred_at + timedelta(hours=23))
        assert not conversation.is_service_window_open(
            now=message.occurred_at + timedelta(hours=25)
        )

    def test_redelivered_identical_body_is_deduplicated(
        self, post_webhook: Any, phone: Any
    ) -> None:
        payload = load_fixture("inbound_text.json", **ids(phone))
        post_webhook(payload)
        post_webhook(payload)
        assert WebhookEvent.objects.count() == 1
        assert Message.objects.count() == 1

    def test_same_message_in_different_batch_is_not_duplicated(
        self, post_webhook: Any, phone: Any
    ) -> None:
        payload = load_fixture("inbound_text.json", **ids(phone))
        post_webhook(payload)
        payload["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"] = "Renamed"
        post_webhook(payload)  # different bytes -> new raw event, same wamid
        assert WebhookEvent.objects.count() == 2
        assert Message.objects.count() == 1
        assert Conversation.objects.get().unread_count == 1

    def test_bsuid_only_sender_and_media_metadata(self, post_webhook: Any, phone: Any) -> None:
        post_webhook(load_fixture("inbound_bsuid_only.json", **ids(phone)))
        message = Message.objects.get()
        assert message.message_type == "image"
        assert message.contact.phone_number == ""
        attachment = message.attachments.get()
        assert attachment.provider_media_id == "1234567890"
        assert attachment.storage_status == "pending"

    def test_unknown_phone_number_is_partial_not_failed(self, post_webhook: Any) -> None:
        payload = load_fixture(
            "inbound_text.json", WABA_ID="1", PHONE_NUMBER_ID="999", WAMID="w", TS=str(TS)
        )
        assert post_webhook(payload).status_code == 200
        event = WebhookEvent.objects.get()
        assert event.status == WebhookEvent.Status.PARTIAL
        assert Message.objects.count() == 0

    def test_tenant_is_derived_from_phone_number(self, post_webhook: Any, phone: Any) -> None:
        other_org = factories.make_organization()
        other_phone = factories.make_phone_number(factories.make_waba(other_org))
        post_webhook(load_fixture("inbound_text.json", **ids(other_phone, wamid="wamid.OTHER")))
        message = Message.objects.get()
        assert message.organization_id == other_org.pk
        assert Contact.objects.for_organization(phone.organization).count() == 0


class TestStatuses:
    def _status(self, post_webhook: Any, phone: Any, wamid: str, status: str, ts: int) -> None:
        post_webhook(load_fixture("status.json", **ids(phone, wamid=wamid, ts=ts, STATUS=status)))

    def test_forward_progression_and_pricing(self, post_webhook: Any, phone: Any) -> None:
        message = make_outbound(phone, "wamid.OUT1")
        self._status(post_webhook, phone, "wamid.OUT1", "sent", TS)
        self._status(post_webhook, phone, "wamid.OUT1", "delivered", TS + 5)
        self._status(post_webhook, phone, "wamid.OUT1", "read", TS + 60)
        message.refresh_from_db()
        assert message.status == MessageStatus.READ
        assert message.sent_at and message.delivered_at and message.read_at
        assert message.billable is True
        assert message.pricing_category == "utility"
        assert MessageStatusEvent.objects.filter(message=message).count() == 3

    def test_late_delivered_does_not_regress_read(self, post_webhook: Any, phone: Any) -> None:
        message = make_outbound(phone, "wamid.OUT2")
        self._status(post_webhook, phone, "wamid.OUT2", "read", TS + 60)
        self._status(post_webhook, phone, "wamid.OUT2", "delivered", TS + 5)
        message.refresh_from_db()
        assert message.status == MessageStatus.READ
        assert message.delivered_at is not None  # timestamp still recorded
        applied = dict(MessageStatusEvent.objects.values_list("provider_status", "applied"))
        assert applied == {"read": True, "delivered": False}

    def test_failed_records_provider_error(self, post_webhook: Any, phone: Any) -> None:
        message = make_outbound(phone, "wamid.OUT3", status=MessageStatus.SENT)
        post_webhook(load_fixture("status_failed.json", **ids(phone, wamid="wamid.OUT3")))
        message.refresh_from_db()
        assert message.status == MessageStatus.FAILED
        assert message.error_code == "131026"

    def test_duplicate_status_report_recorded_once(self, post_webhook: Any, phone: Any) -> None:
        message = make_outbound(phone, "wamid.OUT4")
        payload = load_fixture("status.json", **ids(phone, wamid="wamid.OUT4", STATUS="delivered"))
        post_webhook(payload)
        payload["entry"][0]["changes"][0]["value"]["statuses"][0]["pricing"]["type"] = "x"
        post_webhook(payload)  # different bytes, same (message, status, timestamp)
        assert MessageStatusEvent.objects.filter(message=message).count() == 1

    def test_status_before_message_is_retried_then_applied(
        self, post_webhook: Any, phone: Any
    ) -> None:
        self._status(post_webhook, phone, "wamid.RACE", "delivered", TS)
        event = WebhookEvent.objects.get()
        assert event.status == WebhookEvent.Status.FAILED  # awaiting its message
        assert event.next_attempt_at is not None
        message = make_outbound(phone, "wamid.RACE")
        assert process_event(event.pk, trigger="sweeper").status == WebhookEvent.Status.PROCESSED
        message.refresh_from_db()
        assert message.status == MessageStatus.DELIVERED

    def test_status_for_unknown_message_eventually_unroutable(
        self, post_webhook: Any, phone: Any, settings: Any
    ) -> None:
        settings.WEBHOOK_UNMATCHED_STATUS_RETRY_SECONDS = 0
        self._status(post_webhook, phone, "wamid.FOREIGN", "delivered", TS)
        assert WebhookEvent.objects.get().status == WebhookEvent.Status.PARTIAL


class TestReplayAndRecovery:
    def test_replay_is_idempotent(self, post_webhook: Any, phone: Any) -> None:
        post_webhook(load_fixture("inbound_text.json", **ids(phone)))
        event = WebhookEvent.objects.get()
        mark_for_replay(event)
        result = process_event(event.pk, trigger="replay")
        assert result.status == WebhookEvent.Status.PROCESSED
        assert Message.objects.count() == 1
        assert WebhookProcessingAttempt.objects.filter(event=event).count() == 2

    def test_replay_repairs_state_after_data_loss(self, post_webhook: Any, phone: Any) -> None:
        post_webhook(load_fixture("inbound_text.json", **ids(phone)))
        Message.objects.all().delete()
        event = WebhookEvent.objects.get()
        mark_for_replay(event)
        process_event(event.pk, trigger="replay")
        assert Message.objects.count() == 1

    def test_processor_crash_marks_failed_with_backoff(
        self, post_webhook: Any, phone: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("db hiccup")

        monkeypatch.setattr("apps.webhooks.handlers.ingest_inbound_message", boom)
        post_webhook(load_fixture("inbound_text.json", **ids(phone)))
        event = WebhookEvent.objects.get()
        assert event.status == WebhookEvent.Status.FAILED
        assert "db hiccup" in event.last_error
        assert event.next_attempt_at > timezone.now()

    def test_dead_after_max_attempts(
        self, post_webhook: Any, phone: Any, monkeypatch: pytest.MonkeyPatch, settings: Any
    ) -> None:
        settings.WEBHOOK_MAX_PROCESSING_ATTEMPTS = 1
        monkeypatch.setattr(
            "apps.webhooks.handlers.ingest_inbound_message",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
        )
        post_webhook(load_fixture("inbound_text.json", **ids(phone)))
        assert WebhookEvent.objects.get().status == WebhookEvent.Status.DEAD

    def test_event_persisted_even_if_broker_is_down(
        self, post_webhook: Any, phone: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broker_down(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("redis unavailable")

        monkeypatch.setattr("apps.webhooks.tasks.process_webhook_event.delay", broker_down)
        response = post_webhook(load_fixture("inbound_text.json", **ids(phone)))
        assert response.status_code == 200
        event = WebhookEvent.objects.get()
        assert event.status == WebhookEvent.Status.PENDING
        WebhookEvent.objects.filter(pk=event.pk).update(
            received_at=timezone.now() - timedelta(minutes=5)
        )
        assert due_event_ids() == [event.pk]

    def test_raw_payload_is_stored_verbatim(self, post_webhook: Any, phone: Any) -> None:
        payload = load_fixture("inbound_text.json", **ids(phone))
        post_webhook(payload)
        assert WebhookEvent.objects.get().payload == json.loads(json.dumps(payload))
