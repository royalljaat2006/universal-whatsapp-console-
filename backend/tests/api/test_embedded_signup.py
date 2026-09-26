"""Embedded Signup: config endpoint + code exchange -> connected WABA.

Mocked-Meta tests only (FakeGraph transport) — there is no real Meta app
configured in this environment, so these verify our request/response
handling against the documented contract (docs/integrations/meta/
api-assumptions.md), not a live Embedded Signup flow.
"""

from typing import Any

import pytest

from apps.audit.models import AuditLog
from apps.whatsapp.models import (
    ConnectionState,
    ProviderCredential,
    SyncState,
    WhatsAppBusinessAccount,
)
from tests import factories
from tests.conftest import api_client_for
from tests.fakes import FakeGraph, graph_error
from tests.integration.test_domain_services import _connect_routes

pytestmark = pytest.mark.django_db(transaction=True)


def test_config_reports_whether_embedded_signup_is_configured(owner_api: Any) -> None:
    response = owner_api.get("/api/v1/whatsapp/embedded-signup/config/")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "app_id": "test-app-id",
        "config_id": "test-config-id",
        "graph_api_version": "v26.0",
        "configured": True,
    }


def test_config_requires_numbers_manage(organization: Any) -> None:
    agent = factories.make_member(organization, "agent")
    response = api_client_for(agent, organization).get("/api/v1/whatsapp/embedded-signup/config/")
    assert response.status_code == 403


def test_completes_signup_and_runs_the_connection_pipeline(
    owner_api: Any, organization: Any, fake_graph: FakeGraph
) -> None:
    _connect_routes(fake_graph, waba="4242424242")
    fake_graph.on("GET", "/oauth/access_token", {"access_token": "business-token-abcdefghijklmnop"})
    fake_graph.on("POST", "/5550001112/register", {"success": True})
    kiosk = factories.make_business(organization, "Kiosk")

    response = owner_api.post(
        "/api/v1/whatsapp/embedded-signup/complete/",
        {
            "business_id": str(kiosk.pk),
            "waba_id": "4242424242",
            "phone_number_id": "5550001112",
            "code": "AQD_shortlived_code",
        },
        format="json",
    )

    assert response.status_code == 202
    account = WhatsAppBusinessAccount.objects.get(external_id="4242424242")
    assert account.business_id == kiosk.pk
    # eager Celery ran verify -> subscribe -> sync after commit, same as the manual flow.
    assert account.connection_state == ConnectionState.CONNECTED
    assert account.sync_state == SyncState.SUCCEEDED
    assert "embedded_signup_phone_registration_error" not in account.provider_metadata

    credential = account.credential
    assert credential.encrypted_secret  # stored encrypted, never in a plain column
    assert credential.resolve_secret() == "business-token-abcdefghijklmnop"

    exchange_call = next(c for c in fake_graph.calls if c.url.path.endswith("/oauth/access_token"))
    assert dict(exchange_call.url.params) == {
        "client_id": "test-app-id",
        "client_secret": "test-app-secret",
        "code": "AQD_shortlived_code",
    }
    register_call = next(c for c in fake_graph.calls if c.url.path.endswith("/5550001112/register"))
    assert register_call.headers["Authorization"] == "Bearer business-token-abcdefghijklmnop"

    logged = set(AuditLog.objects.values_list("action", flat=True))
    assert {"whatsapp.embedded_signup_completed", "whatsapp.phone_number_connected"} <= logged


def test_expired_code_fails_cleanly_without_creating_anything(
    owner_api: Any, organization: Any, fake_graph: FakeGraph
) -> None:
    fake_graph.on(
        "GET", "/oauth/access_token", graph_error(400, 100, "This authorization code has expired.")
    )
    kiosk = factories.make_business(organization, "Kiosk")

    response = owner_api.post(
        "/api/v1/whatsapp/embedded-signup/complete/",
        {
            "business_id": str(kiosk.pk),
            "waba_id": "111222333",
            "phone_number_id": "5550001112",
            "code": "expired",
        },
        format="json",
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "EMBEDDED_SIGNUP_FAILED"
    assert not WhatsAppBusinessAccount.objects.filter(external_id="111222333").exists()
    assert not ProviderCredential.objects.filter(organization=organization).exists()


def test_phone_registration_failure_does_not_block_the_connection(
    owner_api: Any, organization: Any, fake_graph: FakeGraph
) -> None:
    """The number may already be registered with a different PIN (e.g. moved
    from another integration) — Embedded Signup must still leave the WABA
    connected, since sync can still pick the number up."""
    _connect_routes(fake_graph, waba="4242424242")
    fake_graph.on("GET", "/oauth/access_token", {"access_token": "business-token-abcdefghijklmnop"})
    fake_graph.on(
        "POST",
        "/5550001112/register",
        graph_error(400, 133010, "Two-step verification PIN mismatch."),
    )
    kiosk = factories.make_business(organization, "Kiosk")

    response = owner_api.post(
        "/api/v1/whatsapp/embedded-signup/complete/",
        {
            "business_id": str(kiosk.pk),
            "waba_id": "4242424242",
            "phone_number_id": "5550001112",
            "code": "AQD_shortlived_code",
        },
        format="json",
    )

    assert response.status_code == 202
    account = WhatsAppBusinessAccount.objects.get(external_id="4242424242")
    assert account.connection_state == ConnectionState.CONNECTED  # pipeline still ran
    assert "133010" in account.provider_metadata["embedded_signup_phone_registration_error"]

    logged = set(AuditLog.objects.values_list("action", flat=True))
    assert "whatsapp.embedded_signup_completed" in logged
    assert "whatsapp.phone_number_connected" not in logged


def test_embedded_signup_into_inaccessible_business_is_404(organization: Any) -> None:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")
    manager = factories.make_member(organization, "admin", businesses=[kiosk])
    response = api_client_for(manager, organization).post(
        "/api/v1/whatsapp/embedded-signup/complete/",
        {
            "business_id": str(eps.pk),
            "waba_id": "999999999",
            "phone_number_id": "5550001112",
            "code": "irrelevant",
        },
        format="json",
    )
    assert response.status_code == 404


def test_agent_cannot_complete_embedded_signup(organization: Any) -> None:
    agent = factories.make_member(organization, "agent")
    kiosk = factories.make_business(organization, "Kiosk")
    response = api_client_for(agent, organization).post(
        "/api/v1/whatsapp/embedded-signup/complete/",
        {
            "business_id": str(kiosk.pk),
            "waba_id": "999999999",
            "phone_number_id": "5550001112",
            "code": "irrelevant",
        },
        format="json",
    )
    assert response.status_code == 403
