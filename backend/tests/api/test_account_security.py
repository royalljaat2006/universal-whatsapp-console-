"""Password reset, email verification, and account deletion.

Emails are asserted via django.core.mail.outbox — the test settings force
Celery to run tasks eagerly (CELERY_TASK_ALWAYS_EAGER) and Django's test
runner always captures mail regardless of EMAIL_BACKEND, so this exercises
the real send path, not a mock.
"""

from typing import Any

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from rest_framework.test import APIClient

from apps.identity.api import _uid_for
from apps.identity.models import User
from apps.identity.tokens import email_verification_token
from apps.organizations.models import Membership
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db


def _csrf_client() -> APIClient:
    client = APIClient(enforce_csrf_checks=True)
    client.get("/api/v1/auth/csrf/")
    return client


class TestPasswordReset:
    def test_request_sends_email_for_a_real_account(self, owner: Any) -> None:
        mail.outbox.clear()
        response = APIClient().post(
            "/api/v1/auth/password-reset/", {"email": owner.email}, format="json"
        )
        assert response.status_code == 204
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [owner.email]
        assert "reset-password" in mail.outbox[0].body

    def test_request_is_204_even_for_an_unknown_email_and_sends_nothing(self) -> None:
        mail.outbox.clear()
        response = APIClient().post(
            "/api/v1/auth/password-reset/", {"email": "nobody@example.com"}, format="json"
        )
        assert response.status_code == 204
        assert len(mail.outbox) == 0

    def test_confirm_with_a_real_token_changes_the_password(self, owner: Any) -> None:
        uid = _uid_for(owner)
        token = default_token_generator.make_token(owner)
        response = APIClient().post(
            "/api/v1/auth/password-reset/confirm/",
            {"uid": uid, "token": token, "password": "brand-new-password-99"},
            format="json",
        )
        assert response.status_code == 204
        owner.refresh_from_db()
        assert owner.check_password("brand-new-password-99")

    def test_confirm_with_a_wrong_token_is_rejected(self, owner: Any) -> None:
        uid = _uid_for(owner)
        response = APIClient().post(
            "/api/v1/auth/password-reset/confirm/",
            {"uid": uid, "token": "not-a-real-token", "password": "brand-new-password-99"},
            format="json",
        )
        assert response.status_code == 403

    def test_confirm_with_an_unknown_uid_is_rejected(self) -> None:
        response = APIClient().post(
            "/api/v1/auth/password-reset/confirm/",
            {"uid": "not-a-real-uid", "token": "x", "password": "brand-new-password-99"},
            format="json",
        )
        assert response.status_code == 403

    def test_changing_password_signs_other_sessions_out(self, owner: Any) -> None:
        # A real cookie-based login, not api_client_for's force_authenticate
        # (which bypasses Django's session mechanism entirely and so could
        # never demonstrate session invalidation).
        session_client = _csrf_client()
        token = session_client.cookies["csrftoken"].value
        session_client.post(
            "/api/v1/auth/login/",
            {"email": owner.email, "password": "correct-horse-battery-staple"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        assert session_client.get("/api/v1/auth/me/").status_code == 200

        # login() just updated last_login via Django's update_last_login
        # signal — the token hash includes it, so refetch before making one.
        owner.refresh_from_db()
        uid = _uid_for(owner)
        reset_token = default_token_generator.make_token(owner)
        APIClient().post(
            "/api/v1/auth/password-reset/confirm/",
            {"uid": uid, "token": reset_token, "password": "brand-new-password-99"},
            format="json",
        )
        assert session_client.get("/api/v1/auth/me/").status_code == 401

    def test_reused_token_fails_after_password_already_changed(self, owner: Any) -> None:
        uid = _uid_for(owner)
        token = default_token_generator.make_token(owner)
        first = APIClient().post(
            "/api/v1/auth/password-reset/confirm/",
            {"uid": uid, "token": token, "password": "brand-new-password-99"},
            format="json",
        )
        assert first.status_code == 204
        second = APIClient().post(
            "/api/v1/auth/password-reset/confirm/",
            {"uid": uid, "token": token, "password": "another-password-123"},
            format="json",
        )
        assert second.status_code == 403


class TestEmailVerification:
    def test_registration_sends_a_verification_email(self) -> None:
        mail.outbox.clear()
        client = _csrf_client()
        token = client.cookies["csrftoken"].value
        response = client.post(
            "/api/v1/auth/register/",
            {
                "email": "verify-me@example.com",
                "password": "correct-horse-battery-staple",
                "organization_name": "Verify Co",
            },
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        assert response.status_code == 201
        assert response.json()["is_email_verified"] is False
        assert len(mail.outbox) == 1
        assert "verify-email" in mail.outbox[0].body

    def test_confirm_with_a_real_token_marks_verified(self, owner: Any) -> None:
        assert owner.is_email_verified is False
        uid = _uid_for(owner)
        token = email_verification_token.make_token(owner)
        response = APIClient().post(
            "/api/v1/auth/verify-email/confirm/", {"uid": uid, "token": token}, format="json"
        )
        assert response.status_code == 200
        assert response.json()["is_email_verified"] is True
        owner.refresh_from_db()
        assert owner.is_email_verified is True

    def test_confirm_with_a_wrong_token_is_rejected(self, owner: Any) -> None:
        uid = _uid_for(owner)
        response = APIClient().post(
            "/api/v1/auth/verify-email/confirm/", {"uid": uid, "token": "garbage"}, format="json"
        )
        assert response.status_code == 403

    def test_token_stops_working_once_already_verified(self, owner: Any) -> None:
        uid = _uid_for(owner)
        token = email_verification_token.make_token(owner)
        first = APIClient().post(
            "/api/v1/auth/verify-email/confirm/", {"uid": uid, "token": token}, format="json"
        )
        assert first.status_code == 200
        second = APIClient().post(
            "/api/v1/auth/verify-email/confirm/", {"uid": uid, "token": token}, format="json"
        )
        assert second.status_code == 403

    def test_resend_requires_authentication(self) -> None:
        response = APIClient().post("/api/v1/auth/verify-email/resend/")
        assert response.status_code == 401

    def test_resend_sends_another_email_when_unverified(
        self, owner: Any, organization: Any
    ) -> None:
        mail.outbox.clear()
        response = api_client_for(owner, organization).post("/api/v1/auth/verify-email/resend/")
        assert response.status_code == 204
        assert len(mail.outbox) == 1

    def test_resend_does_nothing_once_already_verified(self, owner: Any, organization: Any) -> None:
        owner.is_email_verified = True
        owner.save(update_fields=["is_email_verified"])
        mail.outbox.clear()
        response = api_client_for(owner, organization).post("/api/v1/auth/verify-email/resend/")
        assert response.status_code == 204
        assert len(mail.outbox) == 0


class TestAccountDeletion:
    def test_requires_authentication(self) -> None:
        response = APIClient().post("/api/v1/auth/account/", {"password": "x"}, format="json")
        assert response.status_code == 401

    def test_wrong_password_is_rejected(self, owner: Any, organization: Any) -> None:
        response = api_client_for(owner, organization).post(
            "/api/v1/auth/account/", {"password": "totally-wrong"}, format="json"
        )
        assert response.status_code == 401
        assert User.objects.filter(pk=owner.pk).exists()

    def test_deletes_a_non_sole_owner_cleanly(self, organization: Any) -> None:
        second_owner = factories.make_member(organization, "owner")
        second_owner_id = second_owner.pk
        response = api_client_for(second_owner, organization).post(
            "/api/v1/auth/account/", {"password": "correct-horse-battery-staple"}, format="json"
        )
        assert response.status_code == 204
        # .delete() clears the in-memory instance's pk (Django's own
        # behavior), so assert against the id captured beforehand.
        assert not User.objects.filter(pk=second_owner_id).exists()
        assert not Membership.objects.filter(user_id=second_owner_id).exists()

    def test_blocks_deleting_the_sole_owner(self, owner: Any, organization: Any) -> None:
        response = api_client_for(owner, organization).post(
            "/api/v1/auth/account/", {"password": "correct-horse-battery-staple"}, format="json"
        )
        assert response.status_code == 409
        assert organization.name in response.json()["error"]["message"]
        assert User.objects.filter(pk=owner.pk).exists()

    def test_deletion_is_audit_logged_with_actor_set_null_afterwards(
        self, organization: Any
    ) -> None:
        second_owner = factories.make_member(organization, "owner")
        client = api_client_for(second_owner, organization)
        client.post(
            "/api/v1/auth/account/", {"password": "correct-horse-battery-staple"}, format="json"
        )
        from apps.audit.models import AuditLog

        entry = AuditLog.objects.filter(action="auth.account_deleted").latest("created_at")
        assert entry.actor_id is None
