"""API keys: management (organization-scoped, api_keys.manage) and
Bearer-token authentication reusing the existing tenant/permission machinery."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.apikeys.models import ApiKey
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db


def _bearer_client(raw_key: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_key}")
    return client


class TestManagement:
    def test_owner_creates_key_shown_once_and_list_never_exposes_it(
        self, owner: Any, organization: Any
    ) -> None:
        client = api_client_for(owner, organization)
        response = client.post(
            "/api/v1/api-keys/",
            {"name": "CI integration", "scopes": ["contacts.view", "conversations.view"]},
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["key"].startswith("wcapi_")
        assert body["key_prefix"] in body["key"]

        listed = client.get("/api/v1/api-keys/").json()
        assert listed[0]["id"] == body["id"]
        assert "key" not in listed[0]
        assert "key_hash" not in listed[0]

    def test_agent_cannot_manage_keys(self, organization: Any) -> None:
        agent = factories.make_member(organization, "agent")
        response = api_client_for(agent, organization).post(
            "/api/v1/api-keys/", {"name": "x", "scopes": ["contacts.view"]}, format="json"
        )
        assert response.status_code == 403

    def test_unknown_scope_is_rejected(self, owner: Any, organization: Any) -> None:
        response = api_client_for(owner, organization).post(
            "/api/v1/api-keys/", {"name": "x", "scopes": ["not.a.real.permission"]}, format="json"
        )
        assert response.status_code == 400

    def test_revoke(self, owner: Any, organization: Any) -> None:
        client = api_client_for(owner, organization)
        created = client.post(
            "/api/v1/api-keys/", {"name": "temp", "scopes": ["contacts.view"]}, format="json"
        ).json()
        response = client.delete(f"/api/v1/api-keys/{created['id']}/")
        assert response.status_code == 204
        assert ApiKey.objects.get(pk=created["id"]).revoked_at is not None

    def test_cannot_revoke_across_tenants(self, owner: Any, organization: Any) -> None:
        client = api_client_for(owner, organization)
        created = client.post(
            "/api/v1/api-keys/", {"name": "temp", "scopes": ["contacts.view"]}, format="json"
        ).json()
        other_owner_client = api_client_for(owner, factories.make_organization(owner=owner))
        response = other_owner_client.delete(f"/api/v1/api-keys/{created['id']}/")
        assert response.status_code == 404


class TestAuthentication:
    def _create_key(self, owner: Any, organization: Any, scopes: list[str]) -> str:
        return (
            api_client_for(owner, organization)
            .post("/api/v1/api-keys/", {"name": "k", "scopes": scopes}, format="json")
            .json()["key"]
        )

    def test_key_authenticates_and_allows_scoped_action(
        self, owner: Any, organization: Any
    ) -> None:
        raw_key = self._create_key(owner, organization, ["contacts.view"])
        client = _bearer_client(raw_key)
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw_key}", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        response = client.get("/api/v1/contacts/")
        assert response.status_code == 200

    def test_key_denies_out_of_scope_action_even_though_owner_role_allows_it(
        self, owner: Any, organization: Any
    ) -> None:
        # Owner's role permits everything; the key's own scopes narrow it.
        raw_key = self._create_key(owner, organization, ["contacts.view"])
        client = _bearer_client(raw_key)
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw_key}", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        response = client.get("/api/v1/audit-logs/")
        assert response.status_code == 403

    def test_revoked_key_fails_authentication(self, owner: Any, organization: Any) -> None:
        client = api_client_for(owner, organization)
        created = client.post(
            "/api/v1/api-keys/", {"name": "k", "scopes": ["contacts.view"]}, format="json"
        ).json()
        client.delete(f"/api/v1/api-keys/{created['id']}/")
        response = _bearer_client(created["key"]).get(
            "/api/v1/contacts/", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        assert response.status_code == 403

    def test_expired_key_fails_authentication(self, owner: Any, organization: Any) -> None:
        raw_key = self._create_key(owner, organization, ["contacts.view"])
        ApiKey.objects.update(expires_at=timezone.now() - timedelta(days=1))
        response = _bearer_client(raw_key).get(
            "/api/v1/contacts/", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        assert response.status_code == 403

    def test_garbage_key_fails_authentication(self) -> None:
        response = _bearer_client("wcapi_not-a-real-key").get("/api/v1/contacts/")
        assert response.status_code == 403

    def test_key_cannot_reach_a_different_organization(self, owner: Any, organization: Any) -> None:
        raw_key = self._create_key(owner, organization, ["contacts.view"])
        other = factories.make_organization()
        response = _bearer_client(raw_key).get(
            "/api/v1/contacts/", HTTP_X_ORGANIZATION_ID=str(other.pk)
        )
        assert response.status_code == 404

    def test_key_stops_working_once_its_creator_is_suspended(
        self, owner: Any, organization: Any
    ) -> None:
        raw_key = self._create_key(owner, organization, ["contacts.view"])
        owner.memberships.update(status="suspended")
        response = _bearer_client(raw_key).get(
            "/api/v1/contacts/", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        assert response.status_code == 404
