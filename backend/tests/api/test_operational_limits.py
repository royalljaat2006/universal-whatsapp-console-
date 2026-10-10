"""Per-organization operational safeguards (core.limits): reachable, scoped to
the organization that hit them, configurable, and disable-able with 0."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.apikeys.models import ApiKey
from apps.organizations.models import Invitation
from apps.reports.models import ExportJob
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db


def _key(client: APIClient, name: str) -> Any:
    return client.post(
        "/api/v1/api-keys/", {"name": name, "scopes": ["contacts.view"]}, format="json"
    )


class TestApiKeyCap:
    def test_blocks_at_the_cap_with_a_clear_error(
        self, owner: Any, organization: Any, settings: Any
    ) -> None:
        settings.MAX_ACTIVE_API_KEYS_PER_ORG = 2
        client = api_client_for(owner, organization)
        assert _key(client, "one").status_code == 201
        assert _key(client, "two").status_code == 201
        blocked = _key(client, "three")
        assert blocked.status_code == 409
        error = blocked.json()["error"]
        assert error["code"] == "OPERATIONAL_LIMIT_REACHED"
        assert error["details"]["limit"] == 2
        assert ApiKey.objects.filter(organization=organization).count() == 2

    def test_revoking_frees_a_slot(self, owner: Any, organization: Any, settings: Any) -> None:
        settings.MAX_ACTIVE_API_KEYS_PER_ORG = 1
        client = api_client_for(owner, organization)
        created = _key(client, "one")
        assert _key(client, "two").status_code == 409
        assert client.delete(f"/api/v1/api-keys/{created.json()['id']}/").status_code in (200, 204)
        assert _key(client, "two").status_code == 201

    def test_one_organization_hitting_the_cap_does_not_affect_another(
        self, owner: Any, organization: Any, settings: Any
    ) -> None:
        settings.MAX_ACTIVE_API_KEYS_PER_ORG = 1
        assert _key(api_client_for(owner, organization), "a").status_code == 201
        other_owner = factories.make_user()
        other = factories.make_organization(owner=other_owner)
        assert _key(api_client_for(other_owner, other), "b").status_code == 201

    def test_zero_disables_the_guard(self, owner: Any, organization: Any, settings: Any) -> None:
        settings.MAX_ACTIVE_API_KEYS_PER_ORG = 0
        client = api_client_for(owner, organization)
        assert all(_key(client, f"k{i}").status_code == 201 for i in range(5))


class TestInvitationCap:
    def _invite(self, client: APIClient, email: str) -> Any:
        return client.post(
            "/api/v1/organizations/current/invitations/",
            {"email": email, "role_key": "agent"},
            format="json",
        )

    def test_blocks_at_the_cap(self, owner: Any, organization: Any, settings: Any) -> None:
        settings.MAX_PENDING_INVITATIONS_PER_ORG = 2
        client = api_client_for(owner, organization)
        assert self._invite(client, "a@example.com").status_code == 201
        assert self._invite(client, "b@example.com").status_code == 201
        blocked = self._invite(client, "c@example.com")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "OPERATIONAL_LIMIT_REACHED"
        assert Invitation.objects.filter(organization=organization).count() == 2


class TestExportCap:
    def test_blocks_when_too_many_exports_are_in_progress(
        self, owner: Any, organization: Any, settings: Any
    ) -> None:
        settings.MAX_ACTIVE_EXPORTS_PER_ORG = 1
        business = factories.make_business(organization)
        ExportJob.objects.create(
            organization=organization,
            business=business,
            kind=ExportJob.Kind.CONTACTS,
            params={},
            filename="x.csv",
            status=ExportJob.Status.RUNNING,
            requested_by=owner,
        )
        client = api_client_for(owner, organization, business)
        response = client.post(
            "/api/v1/exports/",
            {"kind": ExportJob.Kind.CONTACTS, "params": {}},
            format="json",
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "OPERATIONAL_LIMIT_REACHED"
