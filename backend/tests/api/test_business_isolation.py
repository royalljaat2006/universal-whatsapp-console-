"""TEST C — business isolation, enforced server-side.

Organization "Eko" has two businesses, Kiosk and EPS, each with its own WABA,
numbers, contacts, conversations and messages (created through the real
inbound webhook path). A Kiosk-only agent must never see EPS data and vice
versa; owners see both and can narrow with X-Business-ID.
"""

import json
from typing import Any

import pytest

from apps.contacts.models import Contact
from apps.conversations.models import Conversation
from apps.messaging.models import Message
from tests import factories
from tests.conftest import api_client_for, load_fixture, sign

pytestmark = pytest.mark.django_db(transaction=True)

SAME_CUSTOMER = "16505551234"  # the same WhatsApp user writes to both businesses


def _inbound(client: Any, phone: Any, wamid: str) -> None:
    payload = load_fixture(
        "inbound_text.json",
        WABA_ID=phone.business_account.external_id,
        PHONE_NUMBER_ID=phone.external_id,
        WAMID=wamid,
        TS="1780000000",
    )
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhooks/v1/meta/whatsapp/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sign(body),
    )
    assert response.status_code == 200


@pytest.fixture
def world(client: Any, owner: Any, organization: Any) -> dict[str, Any]:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")
    kiosk_phones = [
        factories.make_phone_number(factories.make_waba(organization, business=kiosk))
        for _ in range(2)
    ]
    eps_phone = factories.make_phone_number(factories.make_waba(organization, business=eps))
    _inbound(client, kiosk_phones[0], "wamid.K1")
    _inbound(client, eps_phone, "wamid.E1")
    kiosk_agent = factories.make_member(organization, "agent", businesses=[kiosk])
    eps_agent = factories.make_member(organization, "agent", businesses=[eps])
    return {
        "kiosk": kiosk,
        "eps": eps,
        "kiosk_phones": kiosk_phones,
        "eps_phone": eps_phone,
        "kiosk_agent": api_client_for(kiosk_agent, organization),
        "eps_agent": api_client_for(eps_agent, organization),
        "owner": api_client_for(owner, organization),
    }


def _ids(response: Any) -> set[str]:
    body = response.json()
    rows = body["results"] if isinstance(body, dict) else body
    return {row["id"] for row in rows}


def test_same_customer_is_a_separate_contact_per_business(world: dict[str, Any]) -> None:
    contacts = Contact.objects.filter(phone_number=SAME_CUSTOMER)
    assert {c.business_id for c in contacts} == {world["kiosk"].pk, world["eps"].pk}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/businesses/",
        "/api/v1/whatsapp/accounts/",
        "/api/v1/whatsapp/numbers/",
        "/api/v1/contacts/",
        "/api/v1/conversations/",
        "/api/v1/messages/",
    ],
)
def test_kiosk_agent_sees_only_kiosk(world: dict[str, Any], path: str) -> None:
    kiosk_rows = _ids(world["owner"].get(path, HTTP_X_BUSINESS_ID=str(world["kiosk"].pk)))
    eps_rows = _ids(world["owner"].get(path, HTTP_X_BUSINESS_ID=str(world["eps"].pk)))
    assert kiosk_rows and eps_rows and not (kiosk_rows & eps_rows)

    seen_by_kiosk_agent = _ids(world["kiosk_agent"].get(path))
    seen_by_eps_agent = _ids(world["eps_agent"].get(path))
    assert seen_by_kiosk_agent == kiosk_rows
    assert seen_by_eps_agent == eps_rows


def test_foreign_business_header_is_404(world: dict[str, Any]) -> None:
    response = world["kiosk_agent"].get(
        "/api/v1/conversations/", HTTP_X_BUSINESS_ID=str(world["eps"].pk)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_foreign_detail_objects_are_404(world: dict[str, Any]) -> None:
    eps_conversation = Conversation.objects.get(business=world["eps"])
    eps_message = Message.objects.get(business=world["eps"])
    client = world["kiosk_agent"]
    assert client.get(f"/api/v1/conversations/{eps_conversation.pk}/").status_code == 404
    assert client.get(f"/api/v1/messages/{eps_message.pk}/").status_code == 404
    assert client.get(f"/api/v1/whatsapp/numbers/{world['eps_phone'].pk}/").status_code == 404
    assert client.post(f"/api/v1/conversations/{eps_conversation.pk}/read/").status_code == 404


def test_cannot_send_from_or_into_foreign_business(world: dict[str, Any]) -> None:
    eps_conversation = Conversation.objects.get(business=world["eps"])
    client = world["kiosk_agent"]
    by_conversation = client.post(
        "/api/v1/messages/",
        {"conversation_id": str(eps_conversation.pk), "type": "text", "text": {"body": "hi"}},
        format="json",
    )
    by_number = client.post(
        "/api/v1/messages/",
        {
            "phone_number_id": str(world["eps_phone"].pk),
            "to": SAME_CUSTOMER,
            "type": "text",
            "text": {"body": "hi"},
        },
        format="json",
    )
    assert by_conversation.status_code == 404
    assert by_number.status_code == 404
    assert Message.objects.filter(direction="outbound").count() == 0


def test_business_header_must_match_sending_number(world: dict[str, Any]) -> None:
    """Owner in Kiosk context cannot send from an EPS number by id."""
    response = world["owner"].post(
        "/api/v1/messages/",
        {
            "phone_number_id": str(world["eps_phone"].pk),
            "to": SAME_CUSTOMER,
            "type": "text",
            "text": {"body": "hi"},
        },
        format="json",
        HTTP_X_BUSINESS_ID=str(world["kiosk"].pk),
    )
    assert response.status_code == 404


def test_agent_cannot_manage_businesses(world: dict[str, Any]) -> None:
    response = world["kiosk_agent"].post("/api/v1/businesses/", {"name": "New"}, format="json")
    assert response.status_code == 403


def test_member_without_grants_sees_nothing(organization: Any, world: dict[str, Any]) -> None:
    user = factories.make_member(organization, "agent", businesses=[], all_businesses=False)
    client = api_client_for(user, organization)
    assert _ids(client.get("/api/v1/businesses/")) == set()
    assert _ids(client.get("/api/v1/conversations/")) == set()


@pytest.mark.parametrize(("viewer", "target"), [("kiosk", "eps"), ("eps", "kiosk")])
def test_detail_isolation_is_symmetric(world: dict[str, Any], viewer: str, target: str) -> None:
    """Kiosk -> EPS and EPS -> Kiosk: every foreign object is a 404, never data."""
    client = world[f"{viewer}_agent"]
    conversation = Conversation.objects.get(business=world[target])
    message = Message.objects.get(business=world[target])
    contact = Contact.objects.get(business=world[target])
    asset = factories.make_media_asset(world[target])
    paths = [
        f"/api/v1/conversations/{conversation.pk}/",
        f"/api/v1/conversations/{conversation.pk}/assignments/",
        f"/api/v1/messages/{message.pk}/",
        f"/api/v1/messages/{message.pk}/status-history/",
        f"/api/v1/contacts/{contact.pk}/",
        f"/api/v1/contacts/{contact.pk}/notes/",
        f"/api/v1/contacts/{contact.pk}/history/",
        f"/api/v1/media/{asset.pk}/",
        f"/api/v1/media/{asset.pk}/content/",
    ]
    for path in paths:
        assert client.get(path).status_code == 404, path
    assert (
        client.get("/api/v1/conversations/", HTTP_X_BUSINESS_ID=str(world[target].pk)).status_code
        == 404
    )
    assert client.post(
        f"/api/v1/conversations/{conversation.pk}/assign/", {"membership_id": None}, format="json"
    ).status_code in (403, 404)
