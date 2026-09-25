"""Platform (super-admin) console: cross-tenant, gated on is_staff — the same
flag that already gates /health/dependencies and the Django admin site."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.organizations.models import Organization
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
