"""Business / credential / WABA / phone-number / conversation management API."""

from typing import Any

import pytest

from apps.businesses.models import Business
from apps.conversations.models import Conversation
from apps.whatsapp.models import ConnectionState, SyncState, WhatsAppBusinessAccount
from tests import factories
from tests.conftest import api_client_for
from tests.fakes import FakeGraph

pytestmark = pytest.mark.django_db(transaction=True)


def test_create_and_update_business(owner_api: Any, organization: Any) -> None:
    created = owner_api.post(
        "/api/v1/businesses/", {"name": "Kiosk", "description": "Kiosk line"}, format="json"
    )
    assert created.status_code == 201
    body = created.json()
    assert body["slug"] == "kiosk" and body["phone_number_count"] == 0
    duplicate = owner_api.post("/api/v1/businesses/", {"name": "Kiosk"}, format="json")
    assert duplicate.status_code == 409
    patched = owner_api.patch(
        f"/api/v1/businesses/{body['id']}/", {"description": "Kiosk business"}, format="json"
    )
    assert patched.status_code == 200 and patched.json()["description"] == "Kiosk business"
    assert Business.objects.filter(organization=organization).count() == 1


class TestCredentials:
    def test_env_reference_and_encrypted_token_are_write_only(self, owner_api: Any) -> None:
        by_ref = owner_api.post(
            "/api/v1/whatsapp/credentials/",
            {"label": "prod", "secret_reference": f"env:{factories.TEST_TOKEN_ENV}"},
            format="json",
        )
        by_token = owner_api.post(
            "/api/v1/whatsapp/credentials/",
            {"label": "pasted", "access_token": factories.TEST_TOKEN},
            format="json",
        )
        assert by_ref.status_code == 201 and by_token.status_code == 201
        assert by_token.json()["backend"] == "encrypted"
        assert by_token.json()["fingerprint"].endswith(factories.TEST_TOKEN[-4:])
        listing = str(owner_api.get("/api/v1/whatsapp/credentials/").json())
        assert factories.TEST_TOKEN not in listing
        assert factories.TEST_TOKEN_ENV not in listing

    def test_disallowed_reference_is_400(self, owner_api: Any) -> None:
        response = owner_api.post(
            "/api/v1/whatsapp/credentials/",
            {"label": "x", "secret_reference": "env:DJANGO_SECRET_KEY"},
            format="json",
        )
        assert response.status_code == 400

    def test_agent_cannot_manage_credentials(self, organization: Any) -> None:
        agent = factories.make_member(organization, "agent")
        response = api_client_for(agent, organization).get("/api/v1/whatsapp/credentials/")
        assert response.status_code == 403


def test_connect_waba_via_api_runs_async_pipeline(
    owner_api: Any, organization: Any, fake_graph: FakeGraph
) -> None:
    from tests.integration.test_domain_services import _connect_routes

    _connect_routes(fake_graph, waba="4242424242")
    kiosk = factories.make_business(organization, "Kiosk")
    credential = owner_api.post(
        "/api/v1/whatsapp/credentials/",
        {"label": "prod", "secret_reference": f"env:{factories.TEST_TOKEN_ENV}"},
        format="json",
    ).json()
    response = owner_api.post(
        "/api/v1/whatsapp/accounts/",
        {"business_id": kiosk.pk, "external_id": "4242424242", "credential_id": credential["id"]},
        format="json",
    )
    # 202: provider work is queued. (Eager Celery in tests runs it at commit,
    # so the body may already show the final state; in production it is pending.)
    assert response.status_code == 202
    account = WhatsAppBusinessAccount.objects.get(external_id="4242424242")
    # eager Celery ran verify -> subscribe -> sync after commit
    assert account.connection_state == ConnectionState.CONNECTED
    assert account.sync_state == SyncState.SUCCEEDED
    numbers = owner_api.get("/api/v1/whatsapp/numbers/", HTTP_X_BUSINESS_ID=str(kiosk.pk)).json()[
        "results"
    ]
    assert {n["external_id"] for n in numbers} == {"p1", "p2"}
    assert numbers[0]["business_account_connection_state"] == "connected"


def test_connect_waba_into_inaccessible_business_is_404(organization: Any) -> None:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")
    manager = factories.make_member(organization, "admin", businesses=[kiosk])
    credential = factories.make_waba(organization, business=kiosk).credential
    response = api_client_for(manager, organization).post(
        "/api/v1/whatsapp/accounts/",
        {"business_id": eps.pk, "external_id": "999999999", "credential_id": credential.pk},
        format="json",
    )
    assert response.status_code == 404


def test_phone_number_local_fields_only(owner_api: Any, phone: Any) -> None:
    response = owner_api.patch(
        f"/api/v1/whatsapp/numbers/{phone.pk}/",
        {"label": "Support", "status": "flagged", "display_phone_number": "+0"},
        format="json",
    )
    assert response.status_code == 200
    phone.refresh_from_db()
    assert phone.label == "Support"
    assert phone.status == "connected"  # provider-owned, unchanged
    assert phone.display_phone_number != "+0"


def test_conversation_status_and_read(owner_api: Any, phone: Any) -> None:
    factories.open_service_window(phone, "16505551234")
    conversation = Conversation.objects.get()
    Conversation.objects.filter(pk=conversation.pk).update(
        unread_count=3, last_message_at=conversation.created_at
    )
    patched = owner_api.patch(
        f"/api/v1/conversations/{conversation.pk}/", {"status": "resolved"}, format="json"
    )
    assert patched.status_code == 200 and patched.json()["status"] == "resolved"
    invalid = owner_api.patch(
        f"/api/v1/conversations/{conversation.pk}/", {"status": "bogus"}, format="json"
    )
    assert invalid.status_code == 400
    read = owner_api.post(f"/api/v1/conversations/{conversation.pk}/read/")
    assert read.json()["unread_count"] == 0
    listing = owner_api.get("/api/v1/conversations/?status=resolved").json()["results"]
    assert [c["id"] for c in listing] == [str(conversation.pk)]


def test_viewer_cannot_change_conversation_status(organization: Any, phone: Any) -> None:
    factories.open_service_window(phone, "16505551234")
    conversation = Conversation.objects.get()
    viewer = factories.make_member(organization, "viewer")
    response = api_client_for(viewer, organization).patch(
        f"/api/v1/conversations/{conversation.pk}/", {"status": "resolved"}, format="json"
    )
    assert response.status_code == 403
