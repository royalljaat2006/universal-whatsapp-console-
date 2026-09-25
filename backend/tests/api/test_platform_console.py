"""Platform (super-admin) console: cross-tenant, gated on is_staff — the same
flag that already gates /health/dependencies and the Django admin site."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.organizations.models import Membership, Organization
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db


def _staff_client() -> APIClient:
    staff = factories.make_user(is_staff=True)
    client = APIClient()
    client.force_authenticate(user=staff)
    return client


class TestAccessControl:
    def test_regular_organization_owner_is_forbidden(self, owner: Any, organization: Any) -> None:
        response = api_client_for(owner, organization).get("/api/v1/platform/organizations/")
        assert response.status_code == 403

    def test_unauthenticated_is_401_or_403(self) -> None:
        assert APIClient().get("/api/v1/platform/organizations/").status_code in (401, 403)

    def test_staff_user_is_allowed(self, organization: Any) -> None:
        assert _staff_client().get("/api/v1/platform/organizations/").status_code == 200


class TestOrganizationDirectory:
    def test_lists_organizations_with_counts(self, organization: Any, owner: Any) -> None:
        factories.make_business(organization)
        factories.make_member(organization, "agent")
        data = _staff_client().get("/api/v1/platform/organizations/").json()
        row = next(o for o in data if o["id"] == str(organization.pk))
        assert row["member_count"] == 2  # owner + agent
        assert row["business_count"] == 1

    def test_detail_view(self, organization: Any) -> None:
        response = _staff_client().get(f"/api/v1/platform/organizations/{organization.pk}/")
        assert response.status_code == 200
        assert response.json()["name"] == organization.name

    def test_unknown_organization_is_404(self) -> None:
        import uuid

        assert (
            _staff_client().get(f"/api/v1/platform/organizations/{uuid.uuid4()}/").status_code
            == 404
        )

    def test_suspend_locks_out_members_immediately(self, owner: Any, organization: Any) -> None:
        member_client = api_client_for(owner, organization)
        assert member_client.get("/api/v1/organizations/current/").status_code == 200

        response = _staff_client().patch(
            f"/api/v1/platform/organizations/{organization.pk}/",
            {"status": "suspended"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["status"] == "suspended"

        organization.refresh_from_db()
        assert organization.status == Organization.Status.SUSPENDED
        # The owner's own membership never changed — the organization itself
        # is locked (apps.organizations.services.get_active_membership).
        assert member_client.get("/api/v1/organizations/current/").status_code == 404

    def test_reactivate_restores_access(self, owner: Any, organization: Any) -> None:
        staff = _staff_client()
        staff.patch(
            f"/api/v1/platform/organizations/{organization.pk}/",
            {"status": "suspended"},
            format="json",
        )
        response = staff.patch(
            f"/api/v1/platform/organizations/{organization.pk}/",
            {"status": "active"},
            format="json",
        )
        assert response.status_code == 200
        assert (
            api_client_for(owner, organization).get("/api/v1/organizations/current/").status_code
            == 200
        )

    def test_suspend_is_audit_logged(self, organization: Any) -> None:
        _staff_client().patch(
            f"/api/v1/platform/organizations/{organization.pk}/",
            {"status": "suspended"},
            format="json",
        )
        entries = (
            _staff_client()
            .get(f"/api/v1/platform/audit-logs/?organization_id={organization.pk}")
            .json()["results"]
        )
        assert any(e["action"] == "organization.suspended" for e in entries)


class TestOperationsAndAuditLogs:
    def test_operations_summary_shape(self, organization: Any) -> None:
        body = _staff_client().get("/api/v1/platform/operations/").json()
        assert body["organizations"]["total"] >= 1
        assert set(body["webhooks_last_24h"]) == {"dead", "failed", "processed", "partial"}
        assert "database" in body["infrastructure"]

    def test_audit_logs_span_organizations(self, organization: Any) -> None:
        other = factories.make_organization()
        results = _staff_client().get("/api/v1/platform/audit-logs/").json()["results"]
        seen_orgs = {e["organization"] for e in results if e.get("organization")}
        # organization.created fired for both orgs during fixture setup; the
        # tenant-scoped /api/v1/audit-logs/ could only ever show one.
        assert str(organization.pk) in seen_orgs
        assert str(other.pk) in seen_orgs

    def test_can_filter_by_organization(self, organization: Any) -> None:
        other = factories.make_organization()
        results = (
            _staff_client()
            .get(f"/api/v1/platform/audit-logs/?organization_id={organization.pk}")
            .json()["results"]
        )
        assert all(e.get("organization") == str(organization.pk) for e in results)
        assert str(other.pk) not in {e.get("organization") for e in results}


class TestProvisionOrganization:
    def test_creates_org_with_no_members_and_returns_a_one_time_invite(self) -> None:
        response = _staff_client().post(
            "/api/v1/platform/organizations/",
            {"name": "Acme Co", "owner_email": "founder@acme.example", "role_key": "owner"},
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["member_count"] == 0
        assert body["status"] == "active"
        assert body["invitation"]["email"] == "founder@acme.example"
        assert body["invitation"]["role"] == "owner"
        assert len(body["invitation"]["token"]) > 20

        org = Organization.objects.get(slug=body["slug"])
        assert not Membership.objects.filter(organization=org).exists()

    def test_default_role_is_owner(self) -> None:
        response = _staff_client().post(
            "/api/v1/platform/organizations/",
            {"name": "No Role Specified", "owner_email": "x@example.com"},
            format="json",
        )
        assert response.json()["invitation"]["role"] == "owner"

    def test_can_provision_with_a_restricted_starting_role(self) -> None:
        response = _staff_client().post(
            "/api/v1/platform/organizations/",
            {
                "name": "Restricted Co",
                "owner_email": "restricted@example.com",
                "role_key": "viewer",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["invitation"]["role"] == "viewer"

    def test_invited_user_can_accept_and_gets_exactly_that_role(self) -> None:
        created = (
            _staff_client()
            .post(
                "/api/v1/platform/organizations/",
                {"name": "Accept Me", "owner_email": "acceptor@example.com", "role_key": "manager"},
                format="json",
            )
            .json()
        )
        invite = created["invitation"]
        response = APIClient().post(
            f"/api/v1/invitations/{invite['id']}/accept/",
            {"token": invite["token"], "password": "correct-horse-battery-staple"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["role"] == "manager"

    def test_unknown_role_key_is_rejected(self) -> None:
        response = _staff_client().post(
            "/api/v1/platform/organizations/",
            {"name": "Bad Role", "owner_email": "x@example.com", "role_key": "not-a-real-role"},
            format="json",
        )
        assert response.status_code == 400

    def test_regular_owner_cannot_provision_organizations(
        self, owner: Any, organization: Any
    ) -> None:
        response = api_client_for(owner, organization).post(
            "/api/v1/platform/organizations/",
            {"name": "Sneaky", "owner_email": "x@example.com"},
            format="json",
        )
        assert response.status_code == 403

    def test_provisioning_is_audit_logged(self) -> None:
        created = (
            _staff_client()
            .post(
                "/api/v1/platform/organizations/",
                {"name": "Audited Co", "owner_email": "audited@example.com"},
                format="json",
            )
            .json()
        )
        results = (
            _staff_client()
            .get(f"/api/v1/platform/audit-logs/?organization_id={created['id']}")
            .json()["results"]
        )
        assert any(e["action"] == "organization.created" for e in results)
        assert any(e["action"] == "invitation.created" for e in results)
