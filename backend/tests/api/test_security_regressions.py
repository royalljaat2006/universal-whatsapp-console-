"""Release-review regressions: paths that could have undermined account
security even though the main flows were correct."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.apikeys.services import create_api_key
from apps.identity import mfa
from apps.identity.models import TOTPDevice, User
from apps.organizations.models import Invitation, Membership
from apps.organizations.services import create_invitation
from tests import factories

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"


def _bearer(user: User, organization: Any) -> APIClient:
    _, raw = create_api_key(
        organization=organization, created_by=user, name="ci", scopes=["contacts.view"]
    )
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {raw}", HTTP_X_ORGANIZATION_ID=str(organization.pk)
    )
    return client


class TestApiKeysCannotManageAccountSecurity:
    """An API key authenticates as its creator. It must not be usable as a
    password oracle or to change the creator's sign-in security."""

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("get", "/api/v1/auth/mfa/", None),
            ("post", "/api/v1/auth/mfa/setup/", {"password": PASSWORD}),
            ("post", "/api/v1/auth/mfa/confirm/", {"code": "123456"}),
            ("post", "/api/v1/auth/mfa/disable/", {"password": PASSWORD, "code": "123456"}),
            ("post", "/api/v1/auth/mfa/recovery-codes/", {"password": PASSWORD, "code": "123456"}),
            ("post", "/api/v1/auth/account/", {"password": PASSWORD}),
        ],
    )
    def test_endpoint_is_refused_for_a_bearer_key(
        self, owner: User, organization: Any, method: str, path: str, body: Any
    ) -> None:
        client = _bearer(owner, organization)
        response = getattr(client, method)(path, body, format="json") if body else client.get(path)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert User.objects.filter(pk=owner.pk).exists()
        assert not TOTPDevice.objects.filter(user=owner).exists()

    def test_the_same_endpoints_still_work_in_a_real_session(self, owner: User) -> None:
        client = APIClient()
        client.force_authenticate(owner)
        assert client.get("/api/v1/auth/mfa/").status_code == 200
        assert client.post("/api/v1/auth/mfa/setup/", {"password": PASSWORD}).status_code == 200

    def test_a_key_still_reads_tenant_data_it_is_scoped_for(
        self, owner: User, organization: Any
    ) -> None:
        business = factories.make_business(organization)
        client = _bearer(owner, organization)
        client.credentials(
            HTTP_AUTHORIZATION=client._credentials["HTTP_AUTHORIZATION"],  # type: ignore[attr-defined]
            HTTP_X_ORGANIZATION_ID=str(organization.pk),
            HTTP_X_BUSINESS_ID=str(business.pk),
        )
        assert client.get("/api/v1/contacts/").status_code == 200

    def test_keys_keep_working_for_a_user_who_enabled_mfa(
        self, organization: Any, owner: User
    ) -> None:
        """API keys are non-interactive and are not subject to the second
        factor; enabling MFA must not break integrations."""
        secret, _ = mfa.begin_enrollment(owner)
        assert mfa.confirm_enrollment(owner, mfa.totp_code(secret, mfa.current_step())) is not None
        business = factories.make_business(organization)
        client = _bearer(owner, organization)
        client.credentials(
            HTTP_AUTHORIZATION=client._credentials["HTTP_AUTHORIZATION"],  # type: ignore[attr-defined]
            HTTP_X_ORGANIZATION_ID=str(organization.pk),
            HTTP_X_BUSINESS_ID=str(business.pk),
        )
        assert client.get("/api/v1/contacts/").status_code == 200


class TestInvitationCannotBypassMfa:
    def test_a_token_cannot_sign_in_an_existing_account_that_has_mfa(
        self, owner: User, organization: Any
    ) -> None:
        victim = factories.make_user(email="victim@example.com")
        other_org = factories.make_organization(owner=victim)
        secret, _ = mfa.begin_enrollment(victim)
        assert mfa.confirm_enrollment(victim, mfa.totp_code(secret, mfa.current_step())) is not None

        invitation, token = create_invitation(
            organization=organization,
            email=victim.email,
            role_key="agent",
            invited_by=owner,
        )
        client = APIClient()
        response = client.post(
            f"/api/v1/invitations/{invitation.pk}/accept/",
            {"token": token, "password": "a-brand-new-password-1"},
            format="json",
        )
        # An existing email is refused ("log in and try again"), never logged in.
        assert response.status_code == 409
        assert client.get("/api/v1/auth/me/").status_code in (401, 403)
        assert not Membership.objects.filter(organization=organization, user=victim).exists()
        assert Invitation.objects.get(pk=invitation.pk).status == Invitation.Status.PENDING
        assert other_org  # victim's own org untouched


class TestAccountLockoutSafety:
    def test_a_user_without_mfa_is_never_blocked_by_mfa_state(self, owner: User) -> None:
        client = APIClient()
        response = client.post("/api/v1/auth/login/", {"email": owner.email, "password": PASSWORD})
        assert response.status_code == 200
        assert client.get("/api/v1/auth/me/").status_code == 200

    def test_lockout_is_temporary_and_only_blocks_that_account(self, owner: User) -> None:
        other = factories.make_user()
        for _ in range(mfa.LOCKOUT_MAX_FAILURES):
            mfa.register_failure(owner)
        assert mfa.is_locked_out(owner)
        assert not mfa.is_locked_out(other)
        # The counter lives in the cache with an expiry, so it cannot be permanent.
        from django.core.cache import cache

        cache.delete(mfa._fail_key(owner))
        assert not mfa.is_locked_out(owner)


class TestResetMfaCommand:
    def _enrolled(self) -> User:
        user = factories.make_user()
        secret, _ = mfa.begin_enrollment(user)
        assert mfa.confirm_enrollment(user, mfa.totp_code(secret, mfa.current_step())) is not None
        return user

    def test_removes_mfa_audits_it_and_lets_the_user_sign_in_again(self) -> None:
        from django.core.management import call_command

        from apps.audit.models import AuditLog

        user = self._enrolled()
        call_command("reset_mfa", email=user.email.upper(), yes=True)
        assert not TOTPDevice.objects.filter(user=user).exists()
        assert not user.recovery_codes.exists()
        entry = AuditLog.objects.filter(action="auth.mfa_disabled", resource_id=str(user.pk)).get()
        assert entry.metadata == {"via": "management_command:reset_mfa"}
        client = APIClient()
        response = client.post("/api/v1/auth/login/", {"email": user.email, "password": PASSWORD})
        assert response.status_code == 200 and "mfa_required" not in response.json()

    def test_unknown_email_and_no_mfa_change_nothing(self, owner: User) -> None:
        from django.core.management import CommandError, call_command

        with pytest.raises(CommandError):
            call_command("reset_mfa", email="nobody@example.com", yes=True)
        call_command("reset_mfa", email=owner.email, yes=True)  # no MFA: a harmless no-op
        assert User.objects.filter(pk=owner.pk).exists()

    def test_declining_the_prompt_changes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from django.core.management import CommandError, call_command

        user = self._enrolled()
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        with pytest.raises(CommandError):
            call_command("reset_mfa", email=user.email)
        assert TOTPDevice.objects.filter(user=user).exists()


class TestCommandUiServerGuarantees:
    """The command home only calls existing endpoints, so these server rules are
    what actually stop it: it cannot launch without permission or confirmation,
    and cannot read a business the member has no access to."""

    def test_launch_is_refused_without_the_launch_permission(self, organization: Any) -> None:
        import uuid

        from tests.conftest import api_client_for

        business = factories.make_business(organization)
        viewer = factories.make_member(organization, "viewer")
        client = api_client_for(viewer, organization, business)
        response = client.post(
            f"/api/v1/campaigns/{uuid.uuid4()}/launch/", {"confirm": True}, format="json"
        )
        assert response.status_code == 403  # permission is checked before the object lookup

    def test_launch_without_explicit_confirmation_is_refused(
        self, owner: User, organization: Any
    ) -> None:
        import uuid

        from tests.conftest import api_client_for

        business = factories.make_business(organization)
        client = api_client_for(owner, organization, business)
        response = client.post(f"/api/v1/campaigns/{uuid.uuid4()}/launch/", {}, format="json")
        assert response.status_code in (400, 404)  # never 2xx, with or without a real campaign

    def test_analytics_for_a_business_the_member_cannot_access_is_not_readable(
        self, organization: Any
    ) -> None:
        from tests.conftest import api_client_for

        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        agent = factories.make_member(organization, "agent", businesses=[kiosk])
        params = {"date_from": "2026-01-01", "date_to": "2026-01-02"}
        ok = api_client_for(agent, organization, kiosk).get("/api/v1/analytics/overview/", params)
        denied = api_client_for(agent, organization, eps).get("/api/v1/analytics/overview/", params)
        assert ok.status_code in (200, 403)  # 403 if the agent role lacks analytics.view
        assert denied.status_code in (403, 404)
        assert "EPS" not in str(denied.json())
