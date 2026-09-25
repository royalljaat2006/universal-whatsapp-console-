"""WebSocket auth, business isolation, and end-to-end fan-out of committed
domain events (webhook -> processor -> DB -> channel layer -> socket)."""

from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from apps.realtime.publisher import _send
from apps.realtime.routing import websocket_urlpatterns
from tests import factories
from tests.conftest import load_fixture

pytestmark = pytest.mark.django_db(transaction=True)

app = URLRouter(websocket_urlpatterns)


def communicator(user: Any, organization_id: Any, business_id: Any) -> WebsocketCommunicator:
    comm = WebsocketCommunicator(
        app, f"/ws/v1/realtime/?organization={organization_id}&business={business_id}"
    )
    comm.scope["user"] = user
    return comm


def _connect(user: Any, organization_id: Any, business_id: Any) -> tuple[bool, Any]:
    async def run() -> tuple[bool, Any]:
        comm = communicator(user, organization_id, business_id)
        result = await comm.connect()
        await comm.disconnect()
        return result

    return async_to_sync(run)()


def test_anonymous_rejected() -> None:
    zero = "00000000-0000-0000-0000-000000000000"
    connected, code = _connect(AnonymousUser(), zero, zero)
    assert not connected and code == 4401


def test_non_member_rejected(owner: Any) -> None:
    other = factories.make_organization()
    business = factories.make_business(other)
    connected, code = _connect(owner, other.pk, business.pk)
    assert not connected and code == 4403


def test_member_without_business_access_rejected(organization: Any) -> None:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")
    agent = factories.make_member(organization, "agent", businesses=[kiosk])
    assert _connect(agent, organization.pk, kiosk.pk)[0] is True
    connected, code = _connect(agent, organization.pk, eps.pk)
    assert not connected and code == 4403


def test_socket_receives_only_its_business_events(owner: Any, organization: Any) -> None:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")

    async def run() -> list[Any]:
        comm = communicator(owner, organization.pk, kiosk.pk)
        connected, _ = await comm.connect()
        assert connected
        ready = await comm.receive_json_from()
        await sync_to_async(_send)(eps.pk, "message.created", {"id": "eps-event"})
        await sync_to_async(_send)(kiosk.pk, "message.created", {"id": "kiosk-event"})
        event = await comm.receive_json_from(timeout=2)
        assert await comm.receive_nothing(timeout=0.2)
        await comm.disconnect()
        return [ready, event]

    ready, event = async_to_sync(run)()
    assert ready["event"] == "connection.ready"
    assert ready["data"]["business"] == str(kiosk.pk)
    assert event["data"] == {"id": "kiosk-event"}


def test_inbound_webhook_reaches_socket(owner: Any, organization: Any, client: Any) -> None:
    """TEST A (automated part): signed Meta webhook -> persisted -> processed
    -> committed -> realtime events on the business channel."""
    import json

    from tests.conftest import sign

    kiosk = factories.make_business(organization, "Kiosk")
    phone = factories.make_phone_number(factories.make_waba(organization, business=kiosk))
    payload = load_fixture(
        "inbound_text.json",
        WABA_ID=phone.business_account.external_id,
        PHONE_NUMBER_ID=phone.external_id,
        WAMID="wamid.RT1",
        TS="1780000000",
    )
    body = json.dumps(payload).encode()

    async def run() -> list[Any]:
        comm = communicator(owner, organization.pk, kiosk.pk)
        connected, _ = await comm.connect()
        assert connected
        await comm.receive_json_from()  # connection.ready
        response = await sync_to_async(client.post)(
            "/webhooks/v1/meta/whatsapp",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sign(body),
        )
        assert response.status_code == 200
        received = [await comm.receive_json_from(timeout=3) for _ in range(2)]
        await comm.disconnect()
        return received

    events = async_to_sync(run)()
    names = [e["event"] for e in events]
    assert names == ["message.created", "conversation.created"]
    assert events[0]["data"]["direction"] == "inbound"
    assert events[0]["data"]["business_id"] == str(kiosk.pk)
    assert events[1]["data"]["unread_count"] == 1


def _collect(owner: Any, organization: Any, business: Any, action: Any, count: int) -> list[Any]:
    async def run() -> list[Any]:
        comm = communicator(owner, organization.pk, business.pk)
        connected, _ = await comm.connect()
        assert connected
        await comm.receive_json_from()  # connection.ready
        await sync_to_async(action)()
        received = [await comm.receive_json_from(timeout=3) for _ in range(count)]
        await comm.disconnect()
        return received

    return async_to_sync(run)()


def test_assignment_is_broadcast(owner: Any, organization: Any) -> None:
    from apps.conversations.assignment import assign
    from apps.organizations.models import Membership

    kiosk = factories.make_business(organization, "Kiosk")
    phone = factories.make_phone_number(factories.make_waba(organization, business=kiosk))
    factories.open_service_window(phone, "16505551234")
    conversation = phone.conversations.get()
    membership = Membership.objects.get(user=owner)
    events = _collect(
        owner,
        organization,
        kiosk,
        lambda: assign(conversation, membership_id=membership.pk, actor=owner),
        1,
    )
    assert events[0]["event"] == "conversation.assigned"
    assert events[0]["data"]["assigned_membership_id"] == str(membership.pk)


def test_media_ready_is_broadcast(
    owner: Any, organization: Any, client: Any, fake_graph: Any
) -> None:
    import json

    from tests.conftest import sign
    from tests.fakes import bytes_response

    kiosk = factories.make_business(organization, "Kiosk")
    phone = factories.make_phone_number(factories.make_waba(organization, business=kiosk))
    fake_graph.on(
        "GET",
        "/1234567890",
        {
            "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1",
            "mime_type": "image/png",
        },
    )
    fake_graph.on(
        "GET",
        "lookaside.fbsbx.com/whatsapp_business/attachments/",
        bytes_response(200, factories.PNG_BYTES, "image/png"),
    )
    payload = load_fixture(
        "inbound_bsuid_only.json",
        WABA_ID=phone.business_account.external_id,
        PHONE_NUMBER_ID=phone.external_id,
        WAMID="wamid.RTM",
        TS="1780000000",
    )
    body = json.dumps(payload).encode()
    events = _collect(
        owner,
        organization,
        kiosk,
        lambda: client.post(
            "/webhooks/v1/meta/whatsapp/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sign(body),
        ),
        3,
    )
    names = [e["event"] for e in events]
    assert "message.media_ready" in names
    ready = next(e for e in events if e["event"] == "message.media_ready")
    assert ready["data"]["storage_status"] == "stored"
