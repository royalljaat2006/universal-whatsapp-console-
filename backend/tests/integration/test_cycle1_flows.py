"""Build Cycle 1 flows: inbound persistence details, conversation lifecycle,
outbound reply-by-conversation, outbox recovery, failure handling."""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.db import DatabaseError
from django.utils import timezone

from apps.contacts.models import Contact
from apps.conversations.models import Conversation
from apps.messaging.models import Message
from apps.messaging.services.outbound import requeue_stale_queued_messages
from apps.messaging.state import MessageStatus
from apps.webhooks.models import WebhookEvent
from tests import factories
from tests.conftest import load_fixture, sign
from tests.fakes import FakeGraph

pytestmark = pytest.mark.django_db(transaction=True)

CUSTOMER = "16505551234"
ACCEPTED = {
    "messaging_product": "whatsapp",
    "contacts": [{"input": CUSTOMER, "wa_id": CUSTOMER}],
    "messages": [{"id": "wamid.OUT", "message_status": "accepted"}],
}


def _inbound(post_webhook: Any, phone: Any, wamid: str, ts: int = 1_780_000_000) -> None:
    post_webhook(
        load_fixture(
            "inbound_text.json",
            WABA_ID=phone.business_account.external_id,
            PHONE_NUMBER_ID=phone.external_id,
            WAMID=wamid,
            TS=str(ts),
        )
    )


def test_inbound_persists_full_context(post_webhook: Any, phone: Any) -> None:
    _inbound(post_webhook, phone, "wamid.CTX")
    message = Message.objects.get()
    assert message.business_id == phone.business_id
    assert message.phone_number_id == phone.pk
    assert message.provider_metadata["message"]["id"] == "wamid.CTX"
    assert message.provider_metadata["message"]["text"]["body"].startswith("Does it come")
    contact = Contact.objects.get()
    assert contact.business_id == phone.business_id
    assert contact.last_inbound_at == message.occurred_at
    conversation = Conversation.objects.get()
    assert conversation.business_id == phone.business_id
    assert conversation.last_message_direction == "inbound"
    assert conversation.unread_count == 1


def test_new_inbound_reopens_resolved_conversation(post_webhook: Any, phone: Any) -> None:
    _inbound(post_webhook, phone, "wamid.A")
    Conversation.objects.update(status=Conversation.Status.RESOLVED)
    _inbound(post_webhook, phone, "wamid.B", ts=1_780_000_100)
    conversation = Conversation.objects.get()
    assert conversation.status == Conversation.Status.OPEN
    assert conversation.unread_count == 2
    assert Contact.objects.count() == 1  # never duplicated per message


def test_reply_by_conversation(
    post_webhook: Any, phone: Any, owner_api: Any, fake_graph: FakeGraph
) -> None:
    """TEST B (automated part): inbox reply -> Meta (fake) -> ACCEPTED ->
    status webhooks -> DELIVERED/READ."""
    fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
    now_ts = int(timezone.now().timestamp())
    _inbound(post_webhook, phone, "wamid.IN", ts=now_ts - 60)
    conversation = Conversation.objects.get()

    response = owner_api.post(
        "/api/v1/messages/",
        {"conversation_id": str(conversation.pk), "type": "text", "text": {"body": "Yes, blue"}},
        format="json",
        HTTP_X_BUSINESS_ID=str(phone.business_id),
    )
    assert response.status_code == 202, response.content
    reply = Message.objects.get(direction="outbound")
    assert reply.status == MessageStatus.ACCEPTED
    assert reply.provider_metadata["send_response"]["messages"][0]["id"] == "wamid.OUT"
    [sent] = fake_graph.json_bodies("POST", "/messages")
    assert sent["to"] == CUSTOMER and sent["text"]["body"] == "Yes, blue"
    conversation.refresh_from_db()
    assert conversation.unread_count == 0
    assert conversation.last_message_direction == "outbound"

    for status, delta in (("delivered", 5), ("read", 30), ("sent", 1)):
        post_webhook(
            load_fixture(
                "status.json",
                WABA_ID=phone.business_account.external_id,
                PHONE_NUMBER_ID=phone.external_id,
                WAMID="wamid.OUT",
                TS=str(now_ts + delta),
                STATUS=status,
            )
        )
    reply.refresh_from_db()
    assert reply.status == MessageStatus.READ  # late "sent" did not regress it
    assert reply.sent_at and reply.delivered_at and reply.read_at
    assert list(reply.status_events.values_list("provider_status", flat=True)) == [
        "sent",
        "delivered",
        "read",
    ]


def test_stale_queued_message_is_redispatched(
    owner_api: Any, phone: Any, fake_graph: FakeGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.messaging.tasks import send_outbound_message

    fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
    factories.open_service_window(phone, CUSTOMER)
    real_delay = send_outbound_message.delay

    def broker_down(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr("apps.messaging.tasks.send_outbound_message.delay", broker_down)
    response = owner_api.post(
        "/api/v1/messages/",
        {"phone_number_id": str(phone.pk), "to": CUSTOMER, "type": "text", "text": {"body": "x"}},
        format="json",
    )
    assert response.status_code == 202  # accepted even though the broker is down
    message = Message.objects.get(direction="outbound")
    assert message.status == MessageStatus.QUEUED
    monkeypatch.setattr("apps.messaging.tasks.send_outbound_message.delay", real_delay)
    Message.objects.filter(pk=message.pk).update(
        status_changed_at=timezone.now() - timedelta(hours=1)
    )
    assert requeue_stale_queued_messages() == 1
    message.refresh_from_db()
    assert message.status == MessageStatus.ACCEPTED


def test_webhook_without_trailing_slash(client: Any, phone: Any) -> None:
    payload = load_fixture(
        "inbound_text.json",
        WABA_ID=phone.business_account.external_id,
        PHONE_NUMBER_ID=phone.external_id,
        WAMID="wamid.NS",
        TS="1780000000",
    )
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhooks/v1/meta/whatsapp",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sign(body),
    )
    assert response.status_code == 200
    assert Message.objects.filter(external_id="wamid.NS").exists()
    handshake = client.get(
        "/webhooks/v1/meta/whatsapp",
        {"hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "7"},
    )
    assert handshake.content == b"7"


def test_database_failure_is_not_acknowledged(
    post_webhook: Any, phone: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def db_down(**kwargs: Any) -> None:
        raise DatabaseError("connection refused")

    monkeypatch.setattr("apps.webhooks.views.persist_event", db_down)
    response = post_webhook(
        load_fixture(
            "inbound_text.json",
            WABA_ID=phone.business_account.external_id,
            PHONE_NUMBER_ID=phone.external_id,
            WAMID="wamid.DB",
            TS="1780000000",
        )
    )
    assert response.status_code == 503  # Meta will retry
    assert WebhookEvent.objects.count() == 0


def test_error_tracking_hook_receives_captured_errors(settings: Any) -> None:
    from core import observability

    received: list[Any] = []
    settings.ERROR_TRACKING_HOOK = "tests.integration.test_cycle1_flows:_collect"
    observability._hook.cache_clear()
    _SINK.clear()
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        fp = observability.capture_exception(exc, source="test")
    received = list(_SINK)
    observability._hook.cache_clear()
    assert received and received[0][1]["fingerprint"] == fp
    assert received[0][1]["source"] == "test"


_SINK: list[Any] = []


def _collect(exc: BaseException, data: dict[str, Any]) -> None:
    _SINK.append((exc, data))
