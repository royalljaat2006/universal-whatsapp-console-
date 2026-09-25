"""API contract: auth, tenant isolation, RBAC, error shape, health, pagination."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.contacts.services import ContactIdentifiers, resolve_contact
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db


class TestAuth:
    def test_login_requires_csrf_token(self, owner: Any) -> None:
        client = APIClient(enforce_csrf_checks=True)
        response = client.post(
            "/api/v1/auth/login/",
            {"email": owner.email, "password": "correct-horse-battery-staple"},
            format="json",
        )
        assert response.status_code == 403

        client.get("/api/v1/auth/csrf/")
        token = client.cookies["csrftoken"].value
        response = client.post(
            "/api/v1/auth/login/",
            {"email": owner.email.upper(), "password": "correct-horse-battery-staple"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        assert response.status_code == 200
        assert client.get("/api/v1/auth/me/").json()["email"] == owner.email

    def test_bad_credentials(self, owner: Any) -> None:
        response = APIClient().post(
            "/api/v1/auth/login/", {"email": owner.email, "password": "wrong"}, format="json"
        )
        assert response.status_code == 401
        body = response.json()["error"]
        assert body["code"] == "INVALID_CREDENTIALS"
        assert body["request_id"]

    def test_unauthenticated_is_401_with_error_contract(self) -> None:
        response = APIClient().get("/api/v1/organizations/")
        assert response.status_code == 401
        error = response.json()["error"]
        assert set(error) == {"code", "message", "details", "request_id"}
        assert error["code"] == "AUTHENTICATION_REQUIRED"
        assert response["X-Request-ID"] == error["request_id"]


class TestTenantIsolation:
    def test_my_organizations_lists_only_memberships(self, owner: Any, organization: Any) -> None:
        factories.make_organization()  # not a member
        data = api_client_for(owner).get("/api/v1/organizations/").json()
        assert [m["organization"]["id"] for m in data] == [str(organization.pk)]
        assert "messages.send" in data[0]["permissions"]

    def test_header_required(self, owner: Any) -> None:
        response = api_client_for(owner).get("/api/v1/contacts/")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "ORGANIZATION_REQUIRED"

    def test_foreign_organization_is_404(self, owner: Any) -> None:
        other = factories.make_organization()
        response = api_client_for(owner, other).get("/api/v1/contacts/")
        assert response.status_code == 404

    def test_malformed_organization_header_is_404(self, owner: Any) -> None:
        client = api_client_for(owner)
        client.credentials(HTTP_X_ORGANIZATION_ID="not-a-uuid")
        assert client.get("/api/v1/contacts/").status_code == 404

    def test_list_and_detail_never_leak_other_tenants(self, owner: Any, organization: Any) -> None:
        other = factories.make_organization()
        mine = resolve_contact(
            factories.default_business(organization), ContactIdentifiers(phone_number="16505551111")
        )
        theirs = resolve_contact(
            factories.default_business(other), ContactIdentifiers(phone_number="16505552222")
        )
        client = api_client_for(owner, organization)
        ids = [c["id"] for c in client.get("/api/v1/contacts/").json()["results"]]
        assert ids == [str(mine.pk)]
        assert client.get(f"/api/v1/contacts/{theirs.pk}/").status_code == 404
        phone_other = factories.make_phone_number(factories.make_waba(other))
        assert client.get(f"/api/v1/whatsapp/numbers/{phone_other.pk}/").status_code == 404

    def test_suspended_membership_loses_access(self, organization: Any) -> None:
        agent = factories.make_member(organization, "agent")
        agent.memberships.update(status="suspended")
        assert api_client_for(agent, organization).get("/api/v1/contacts/").status_code == 404


class TestRbac:
    @pytest.mark.parametrize(
        ("role", "path", "expected"),
        [
            ("viewer", "/api/v1/contacts/", 200),
            ("viewer", "/api/v1/audit-logs/", 403),
            ("agent", "/api/v1/audit-logs/", 403),
            ("agent", "/api/v1/conversations/", 200),
            ("manager", "/api/v1/organizations/current/members/", 200),
            ("admin", "/api/v1/audit-logs/", 200),
        ],
    )
    def test_role_permissions(self, organization: Any, role: str, path: str, expected: int) -> None:
        user = factories.make_member(organization, role)
        assert api_client_for(user, organization).get(path).status_code == expected

    def test_sync_requires_numbers_manage(self, organization: Any, waba: Any) -> None:
        agent = factories.make_member(organization, "agent")
        response = api_client_for(agent, organization).post(
            f"/api/v1/whatsapp/accounts/{waba.pk}/sync/"
        )
        assert response.status_code == 403


class TestResources:
    def test_numbers_and_accounts(self, owner_api: Any, phone: Any) -> None:
        accounts = owner_api.get("/api/v1/whatsapp/accounts/").json()["results"]
        assert accounts[0]["phone_number_count"] == 1
        detail = owner_api.get(f"/api/v1/whatsapp/numbers/{phone.pk}/").json()
        assert detail["external_id"] == phone.external_id
        # Only the credential id is exposed; never the reference or the token.
        serialized = str(accounts)
        assert "secret_reference" not in serialized
        assert "PROVIDER_SECRET" not in serialized
        assert factories.TEST_TOKEN not in serialized

    def test_invalid_filter_is_400_not_500(self, owner_api: Any) -> None:
        response = owner_api.get("/api/v1/whatsapp/numbers/?business_account=nope")
        assert response.status_code == 400

    def test_cursor_pagination(self, owner_api: Any, organization: Any) -> None:
        for i in range(3):
            resolve_contact(
                factories.default_business(organization),
                ContactIdentifiers(phone_number=f"1650555000{i}"),
            )
        page = owner_api.get("/api/v1/contacts/?page_size=2").json()
        assert len(page["results"]) == 2 and page["next"]
        rest = owner_api.get(page["next"]).json()
        assert len(rest["results"]) == 1


class TestHealth:
    def test_live(self, client: Any) -> None:
        assert client.get("/health/live").json() == {"status": "ok"}

    def test_ready_checks_database_migrations_cache(self, client: Any) -> None:
        body = client.get("/health/ready").json()
        assert body["status"] == "ok"
        assert set(body["checks"]) == {"database", "migrations", "redis_cache"}

    def test_dependencies_is_staff_only(self, client: Any) -> None:
        assert client.get("/health/dependencies").status_code == 403
