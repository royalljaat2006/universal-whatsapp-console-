"""Team invitations: create/list/revoke (tenant-scoped, members.manage) and
accept (public, token-based — the recipient has no membership yet)."""

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.organizations.models import Invitation, Membership
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db


def _csrf_client() -> APIClient:
    client = APIClient(enforce_csrf_checks=True)
    client.get("/api/v1/auth/csrf/")
    return client


class TestCreateListRevoke:
    def test_owner_can_invite_and_token_is_shown_once(self, owner: Any, organization: Any) -> None:
        mail.outbox.clear()
        client = api_client_for(owner, organization)
        response = client.post(
            "/api/v1/organizations/current/invitations/",
            {"email": "new-hire@example.com", "role_key": "agent"},
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "new-hire@example.com"
        assert body["role"] == "agent"
        assert body["status"] == "pending"
        assert len(body["token"]) > 20

        listed = client.get("/api/v1/organizations/current/invitations/").json()
        assert listed[0]["id"] == body["id"]
        assert "token" not in listed[0]

        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.to == ["new-hire@example.com"]
        assert organization.name in sent.subject
        assert body["accept_path"] in sent.body  # plain-text part, incl. the real token
        assert "Agent" in sent.body
        html_part = sent.alternatives[0][0]
        assert sent.alternatives[0][1] == "text/html"
        assert body["accept_path"] in html_part

    def test_agent_cannot_invite(self, organization: Any) -> None:
        agent = factories.make_member(organization, "agent")
        response = api_client_for(agent, organization).post(
            "/api/v1/organizations/current/invitations/",
            {"email": "x@example.com", "role_key": "agent"},
            format="json",
        )
        assert response.status_code == 403

    def test_duplicate_pending_invite_is_conflict(self, owner: Any, organization: Any) -> None:
        client = api_client_for(owner, organization)
        client.post(
            "/api/v1/organizations/current/invitations/",
            {"email": "dup@example.com", "role_key": "agent"},
            format="json",
        )
        response = client.post(
            "/api/v1/organizations/current/invitations/",
            {"email": "dup@example.com", "role_key": "viewer"},
            format="json",
        )
        assert response.status_code == 409

    def test_inviting_an_existing_member_is_conflict(self, owner: Any, organization: Any) -> None:
        member = factories.make_member(organization, "agent")
        response = api_client_for(owner, organization).post(
            "/api/v1/organizations/current/invitations/",
            {"email": member.email, "role_key": "manager"},
            format="json",
        )
        assert response.status_code == 409

    def test_revoke(self, owner: Any, organization: Any) -> None:
        client = api_client_for(owner, organization)
        created = client.post(
            "/api/v1/organizations/current/invitations/",
            {"email": "revoke-me@example.com", "role_key": "agent"},
            format="json",
        ).json()
        response = client.delete(f"/api/v1/organizations/current/invitations/{created['id']}/")
        assert response.status_code == 204
        assert Invitation.objects.get(pk=created["id"]).status == Invitation.Status.REVOKED

    def test_cannot_revoke_across_tenants(self, owner: Any, organization: Any) -> None:
        client = api_client_for(owner, organization)
        created = client.post(
            "/api/v1/organizations/current/invitations/",
            {"email": "cross@example.com", "role_key": "agent"},
            format="json",
        ).json()
        other_owner_client = api_client_for(owner, factories.make_organization(owner=owner))
        response = other_owner_client.delete(
            f"/api/v1/organizations/current/invitations/{created['id']}/"
        )
        assert response.status_code == 404


class TestAccept:
    def _invite(self, owner: Any, organization: Any, email: str = "invitee@example.com") -> dict:
        return (
            api_client_for(owner, organization)
            .post(
                "/api/v1/organizations/current/invitations/",
                {"email": email, "role_key": "agent"},
                format="json",
            )
            .json()
        )

    def test_preview_shows_org_and_role_without_a_token(
        self, owner: Any, organization: Any
    ) -> None:
        invite = self._invite(owner, organization)
        response = APIClient().get(f"/api/v1/invitations/{invite['id']}/")
        assert response.status_code == 200
        body = response.json()
        assert body["organization_name"] == organization.name
        assert body["role_name"] == "Agent"
        assert body["email"] == "invitee@example.com"

    def test_unknown_invitation_is_404(self) -> None:
        import uuid

        assert APIClient().get(f"/api/v1/invitations/{uuid.uuid4()}/").status_code == 404

    def test_new_user_accepts_and_is_logged_in(self, owner: Any, organization: Any) -> None:
        invite = self._invite(owner, organization)
        client = _csrf_client()
        token = client.cookies["csrftoken"].value
        response = client.post(
            f"/api/v1/invitations/{invite['id']}/accept/",
            {
                "token": invite["token"],
                "password": "correct-horse-battery-staple",
                "full_name": "Invitee",
            },
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        assert response.status_code == 200
        assert response.json()["organization_id"] == str(organization.pk)

        user = User.objects.get(email="invitee@example.com")
        assert Membership.objects.get(user=user, organization=organization).role.key == "agent"
        assert client.get("/api/v1/auth/me/").json()["email"] == "invitee@example.com"

    def test_accept_requires_csrf(self, owner: Any, organization: Any) -> None:
        invite = self._invite(owner, organization)
        response = APIClient(enforce_csrf_checks=True).post(
            f"/api/v1/invitations/{invite['id']}/accept/",
            {"token": invite["token"], "password": "correct-horse-battery-staple"},
            format="json",
        )
        assert response.status_code == 403
        assert not User.objects.filter(email="invitee@example.com").exists()

    def test_existing_authenticated_user_with_matching_email_accepts(
        self, owner: Any, organization: Any
    ) -> None:
        existing = factories.make_user(email="already-has-account@example.com")
        invite = self._invite(owner, organization, email=existing.email)
        client = APIClient()
        client.force_authenticate(user=existing)
        response = client.post(
            f"/api/v1/invitations/{invite['id']}/accept/", {"token": invite["token"]}, format="json"
        )
        assert response.status_code == 200
        assert Membership.objects.filter(user=existing, organization=organization).exists()

    def test_authenticated_user_with_different_email_is_rejected(
        self, owner: Any, organization: Any
    ) -> None:
        invite = self._invite(owner, organization)
        someone_else = factories.make_user(email="someone-else@example.com")
        client = APIClient()
        client.force_authenticate(user=someone_else)
        response = client.post(
            f"/api/v1/invitations/{invite['id']}/accept/", {"token": invite["token"]}, format="json"
        )
        assert response.status_code == 403
        assert not Membership.objects.filter(user=someone_else, organization=organization).exists()

    def test_wrong_token_is_rejected(self, owner: Any, organization: Any) -> None:
        invite = self._invite(owner, organization)
        response = APIClient().post(
            f"/api/v1/invitations/{invite['id']}/accept/",
            {"token": "not-the-real-token", "password": "correct-horse-battery-staple"},
            format="json",
        )
        assert response.status_code == 403

    def test_revoked_invitation_cannot_be_accepted(self, owner: Any, organization: Any) -> None:
        invite = self._invite(owner, organization)
        api_client_for(owner, organization).delete(
            f"/api/v1/organizations/current/invitations/{invite['id']}/"
        )
        response = APIClient().post(
            f"/api/v1/invitations/{invite['id']}/accept/",
            {"token": invite["token"], "password": "correct-horse-battery-staple"},
            format="json",
        )
        assert response.status_code == 403

    def test_expired_invitation_cannot_be_accepted(self, owner: Any, organization: Any) -> None:
        invite = self._invite(owner, organization)
        Invitation.objects.filter(pk=invite["id"]).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        response = APIClient().post(
            f"/api/v1/invitations/{invite['id']}/accept/",
            {"token": invite["token"], "password": "correct-horse-battery-staple"},
            format="json",
        )
        assert response.status_code == 403

    def test_accepting_twice_fails_the_second_time(self, owner: Any, organization: Any) -> None:
        invite = self._invite(owner, organization)
        body = {"token": invite["token"], "password": "correct-horse-battery-staple"}
        first = APIClient().post(f"/api/v1/invitations/{invite['id']}/accept/", body, format="json")
        assert first.status_code == 200
        second = APIClient().post(
            f"/api/v1/invitations/{invite['id']}/accept/", body, format="json"
        )
        assert second.status_code == 403
