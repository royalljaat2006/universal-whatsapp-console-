"""Custom roles: organizations already seed 5 default roles (permissions.py);
this covers creating, editing and deleting roles beyond those (roles.manage)."""

from typing import Any

import pytest

from apps.organizations.models import Role
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db


class TestListCreate:
    def test_lists_the_five_seeded_default_roles(self, owner: Any, organization: Any) -> None:
        data = (
            api_client_for(owner, organization).get("/api/v1/organizations/current/roles/").json()
        )
        assert {r["key"] for r in data} == {"owner", "admin", "manager", "agent", "viewer"}
        owner_row = next(r for r in data if r["key"] == "owner")
        assert "billing.manage" in owner_row["permissions"]
        assert owner_row["is_system"] is True

    def test_agent_cannot_manage_roles(self, organization: Any) -> None:
        agent = factories.make_member(organization, "agent")
        response = api_client_for(agent, organization).get("/api/v1/organizations/current/roles/")
        assert response.status_code == 403

    def test_create_custom_role_with_a_permission_subset(
        self, owner: Any, organization: Any
    ) -> None:
        response = api_client_for(owner, organization).post(
            "/api/v1/organizations/current/roles/",
            {
                "name": "Billing Clerk",
                "description": "Views costs only",
                "permissions": ["billing.view", "analytics.view"],
            },
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["is_system"] is False
        assert set(body["permissions"]) == {"billing.view", "analytics.view"}

    def test_unknown_permission_code_is_rejected(self, owner: Any, organization: Any) -> None:
        response = api_client_for(owner, organization).post(
            "/api/v1/organizations/current/roles/",
            {"name": "Bad Role", "permissions": ["not.a.real.permission"]},
            format="json",
        )
        assert response.status_code == 400

    def test_a_member_with_the_custom_role_gets_exactly_its_permissions(
        self, owner: Any, organization: Any
    ) -> None:
        client = api_client_for(owner, organization)
        role = client.post(
            "/api/v1/organizations/current/roles/",
            {"name": "Billing Clerk", "permissions": ["billing.view"]},
            format="json",
        ).json()
        clerk = factories.make_member(organization, role["key"])
        clerk_client = api_client_for(clerk, organization)
        assert clerk_client.get("/api/v1/contacts/").status_code == 403
        # billing has no HTTP surface of its own yet (see architecture-audit.md);
        # role scoping itself is what's under test here, via has_permission.
        from apps.organizations.models import Membership
        from apps.organizations.services import has_permission

        membership = Membership.objects.get(user=clerk, organization=organization)
        assert has_permission(membership, "billing.view") is True
        assert has_permission(membership, "contacts.view") is False


class TestUpdateDelete:
    def _custom_role(self, owner: Any, organization: Any) -> dict:
        return (
            api_client_for(owner, organization)
            .post(
                "/api/v1/organizations/current/roles/",
                {"name": "Temp Role", "permissions": ["contacts.view"]},
                format="json",
            )
            .json()
        )

    def test_update_permissions(self, owner: Any, organization: Any) -> None:
        role = self._custom_role(owner, organization)
        client = api_client_for(owner, organization)
        response = client.patch(
            f"/api/v1/organizations/current/roles/{role['id']}/",
            {"permissions": ["contacts.view", "conversations.view"]},
            format="json",
        )
        assert response.status_code == 200
        assert set(response.json()["permissions"]) == {"contacts.view", "conversations.view"}

    def test_delete_unused_custom_role(self, owner: Any, organization: Any) -> None:
        role = self._custom_role(owner, organization)
        client = api_client_for(owner, organization)
        response = client.delete(f"/api/v1/organizations/current/roles/{role['id']}/")
        assert response.status_code == 204
        assert not Role.objects.filter(pk=role["id"]).exists()

    def test_cannot_delete_a_system_role(self, owner: Any, organization: Any) -> None:
        owner_role = Role.objects.for_organization(organization).get(key="owner")
        response = api_client_for(owner, organization).delete(
            f"/api/v1/organizations/current/roles/{owner_role.pk}/"
        )
        assert response.status_code == 403
        assert Role.objects.filter(pk=owner_role.pk).exists()

    def test_cannot_delete_a_role_still_assigned_to_a_member(
        self, owner: Any, organization: Any
    ) -> None:
        role = self._custom_role(owner, organization)
        factories.make_member(organization, role["key"])
        response = api_client_for(owner, organization).delete(
            f"/api/v1/organizations/current/roles/{role['id']}/"
        )
        assert response.status_code == 409

    def test_cannot_touch_a_role_in_another_organization(
        self, owner: Any, organization: Any
    ) -> None:
        role = self._custom_role(owner, organization)
        other_client = api_client_for(owner, factories.make_organization(owner=owner))
        response = other_client.patch(
            f"/api/v1/organizations/current/roles/{role['id']}/",
            {"name": "Hijacked"},
            format="json",
        )
        assert response.status_code == 404
