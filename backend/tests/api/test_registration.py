"""Self-serve registration: POST /api/v1/auth/register/ creates a new user
AND a new organization they own. Joining an existing organization goes
through an invitation instead (test_invitations.py)."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.organizations.models import Membership, Organization
from tests import factories

pytestmark = pytest.mark.django_db


def _csrf_client() -> APIClient:
    client = APIClient(enforce_csrf_checks=True)
    client.get("/api/v1/auth/csrf/")
    return client


def _register(client: APIClient, **overrides: Any) -> Any:
    body = {
        "email": "new-owner@example.com",
        "password": "correct-horse-battery-staple",
        "full_name": "New Owner",
        "organization_name": "Acme Co",
        **overrides,
    }
    token = client.cookies["csrftoken"].value
    return client.post("/api/v1/auth/register/", body, format="json", HTTP_X_CSRFTOKEN=token)


class TestRegister:
    def test_creates_user_and_owner_membership(self) -> None:
        response = _register(_csrf_client())
        assert response.status_code == 201
        assert response.json()["email"] == "new-owner@example.com"

        user = User.objects.get(email="new-owner@example.com")
        org = Organization.objects.get(slug="acme-co")
        membership = Membership.objects.get(user=user, organization=org)
        assert membership.role.key == "owner"
        assert membership.status == Membership.Status.ACTIVE

    def test_registration_logs_the_user_in(self) -> None:
        client = _csrf_client()
        _register(client)
        assert client.get("/api/v1/auth/me/").json()["email"] == "new-owner@example.com"

    def test_requires_csrf(self) -> None:
        client = APIClient(enforce_csrf_checks=True)
        response = client.post(
            "/api/v1/auth/register/",
            {
                "email": "x@example.com",
                "password": "correct-horse-battery-staple",
                "organization_name": "X",
            },
            format="json",
        )
        assert response.status_code == 403
        assert not User.objects.filter(email="x@example.com").exists()

    def test_duplicate_email_is_conflict(self) -> None:
        factories.make_user(email="taken@example.com")
        response = _register(_csrf_client(), email="taken@example.com")
        assert response.status_code == 409

    def test_duplicate_organization_name_gets_a_disambiguated_slug(self) -> None:
        factories.make_organization(name="Acme Co")
        response = _register(_csrf_client(), email="second-owner@example.com")
        assert response.status_code == 201
        orgs = Organization.objects.filter(name="Acme Co")
        assert orgs.count() == 2
        assert len({o.slug for o in orgs}) == 2

    def test_password_too_short_is_rejected(self) -> None:
        response = _register(_csrf_client(), password="short")
        assert response.status_code == 400
