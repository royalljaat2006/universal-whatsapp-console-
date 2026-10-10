"""TOTP multi-factor authentication: correctness against the RFC 6238 vectors,
the two-step sign-in, replay protection, brute-force resistance, recovery
codes, and - most importantly - that the second factor cannot be skipped by
a login path that bypasses it (Django admin session, other sessions, the
WebSocket)."""

import base64
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.audit.models import AuditLog
from apps.identity import mfa
from apps.identity.models import RecoveryCode, TOTPDevice, User
from apps.realtime.routing import websocket_urlpatterns
from tests import factories

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"
BASE = "/api/v1/auth"


def _code(secret: str, step: int | None = None) -> str:
    return mfa.totp_code(secret, mfa.current_step() if step is None else step)


@pytest.fixture
def frozen_step(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Deterministic TOTP clock: tests move ``step[0]`` themselves, so nothing
    depends on being near a 30-second boundary."""
    step = [57_000_000]
    monkeypatch.setattr(mfa, "current_step", lambda now=None: step[0])
    return step


@pytest.fixture
def relaxed_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 'auth' scope (10/min) would mask the per-account lockout these tests
    are about; lift it for the tests that deliberately make many attempts."""
    monkeypatch.setattr(
        ScopedRateThrottle, "THROTTLE_RATES", {"auth": "10000/min", "user": "10000/min"}
    )


def _enrol(user: User) -> tuple[str, list[str]]:
    secret, _uri = mfa.begin_enrollment(user)
    codes = mfa.confirm_enrollment(user, _code(secret))
    assert codes is not None
    mfa.forget_cached_stamp(user)
    return secret, codes


def _login(client: APIClient, user: User, password: str = PASSWORD) -> Any:
    return client.post(f"{BASE}/login/", {"email": user.email, "password": password})


@pytest.fixture
def enrolled(frozen_step: list[int]) -> tuple[User, str, list[str]]:
    user = factories.make_user()
    factories.make_organization(owner=user)
    secret, codes = _enrol(user)
    frozen_step[0] += 5  # enrolment consumed a step; later codes use later steps
    return user, secret, codes


class TestRfc6238Vectors:
    """Appendix B of RFC 6238 (SHA-1, shared secret '12345678901234567890'),
    truncated to the last six digits."""

    SECRET = base64.b32encode(b"12345678901234567890").decode()

    @pytest.mark.parametrize(
        ("unix_time", "expected"),
        [
            (59, "287082"),
            (1111111109, "081804"),
            (1111111111, "050471"),
            (1234567890, "005924"),
            (2000000000, "279037"),
            (20000000000, "353130"),
        ],
    )
    def test_matches_published_vectors(self, unix_time: int, expected: str) -> None:
        assert mfa.totp_code(self.SECRET, unix_time // 30) == expected

    def test_accepts_adjacent_steps_only(self) -> None:
        now = 1111111109
        step = now // 30
        assert mfa.matching_step(self.SECRET, mfa.totp_code(self.SECRET, step - 1), now) == step - 1
        assert mfa.matching_step(self.SECRET, mfa.totp_code(self.SECRET, step + 1), now) == step + 1
        assert mfa.matching_step(self.SECRET, mfa.totp_code(self.SECRET, step - 2), now) is None
        assert mfa.matching_step(self.SECRET, "12345", now) is None
        assert mfa.matching_step(self.SECRET, "abcdef", now) is None


class TestEnrollment:
    def test_setup_requires_the_current_password(self, owner: Any) -> None:
        client = APIClient()
        client.force_authenticate(owner)
        response = client.post(f"{BASE}/mfa/setup/", {"password": "wrong-password-123"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "REAUTHENTICATION_FAILED"
        assert not TOTPDevice.objects.exists()

    def test_full_enrolment_and_secret_is_encrypted_at_rest(
        self, owner: Any, frozen_step: list[int]
    ) -> None:
        client = APIClient()
        client.force_authenticate(owner)
        setup = client.post(f"{BASE}/mfa/setup/", {"password": PASSWORD})
        assert setup.status_code == 200
        secret = setup.json()["secret"]
        assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
        assert f"secret={secret}" in setup.json()["otpauth_uri"]

        stored = bytes(TOTPDevice.objects.get(user=owner).encrypted_secret)
        assert secret.encode() not in stored

        # Not enforced until confirmed.
        assert mfa.user_has_mfa(User.objects.get(pk=owner.pk)) is False

        bad = client.post(f"{BASE}/mfa/confirm/", {"code": "000000"})
        assert bad.status_code == 401
        assert bad.json()["error"]["code"] == "INVALID_MFA_CODE"

        ok = client.post(f"{BASE}/mfa/confirm/", {"code": _code(secret)})
        assert ok.status_code == 200
        codes = ok.json()["recovery_codes"]
        assert len(codes) == mfa.RECOVERY_CODE_COUNT and len(set(codes)) == len(codes)
        # Only hashes are stored.
        assert not RecoveryCode.objects.filter(code_hash__in=codes).exists()
        assert RecoveryCode.objects.filter(user=owner).count() == mfa.RECOVERY_CODE_COUNT

        status = client.get(f"{BASE}/mfa/").json()
        assert status["enabled"] is True and status["recovery_codes_remaining"] == 10
        assert AuditLog.objects.filter(action="auth.mfa_enrolled").exists()

    def test_cannot_start_enrolment_when_already_enabled(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, _ = enrolled
        client = APIClient()
        client.force_authenticate(user)
        assert client.post(f"{BASE}/mfa/setup/", {"password": PASSWORD}).status_code == 409

    def test_an_unconfirmed_device_does_not_gate_login(self, owner: Any) -> None:
        mfa.begin_enrollment(owner)
        response = _login(APIClient(), owner)
        assert response.status_code == 200
        assert "mfa_required" not in response.json()

    def test_confirmation_cannot_be_replayed_to_enrol_twice(
        self, owner: Any, frozen_step: list[int]
    ) -> None:
        secret, _ = mfa.begin_enrollment(owner)
        code = _code(secret)
        assert mfa.confirm_enrollment(owner, code) is not None
        assert mfa.confirm_enrollment(owner, code) is None


class TestTwoStepLogin:
    def test_password_alone_does_not_sign_in(self, enrolled: tuple[User, str, list[str]]) -> None:
        user, _, _ = enrolled
        client = APIClient()
        response = _login(client, user)
        assert response.status_code == 200
        assert response.json() == {"mfa_required": True}
        assert client.get(f"{BASE}/me/").status_code in (401, 403)

    def test_correct_code_completes_login(
        self, enrolled: tuple[User, str, list[str]], frozen_step: list[int]
    ) -> None:
        user, secret, _ = enrolled
        client = APIClient()
        _login(client, user)
        response = client.post(f"{BASE}/mfa/verify/", {"code": _code(secret)})
        assert response.status_code == 200
        assert response.json()["email"] == user.email
        assert response.json()["mfa_enabled"] is True
        assert client.get(f"{BASE}/me/").status_code == 200
        assert AuditLog.objects.filter(action="auth.login_succeeded").exists()

    def test_wrong_code_is_rejected_and_audited(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, _ = enrolled
        client = APIClient()
        _login(client, user)
        response = client.post(f"{BASE}/mfa/verify/", {"code": "000000"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_MFA_CODE"
        assert client.get(f"{BASE}/me/").status_code in (401, 403)
        assert AuditLog.objects.filter(action="auth.mfa_failed").exists()

    def test_wrong_password_never_reaches_the_second_step(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, _ = enrolled
        response = _login(APIClient(), user, "not-the-password")
        assert response.status_code == 401
        assert "mfa_required" not in response.json()

    def test_verify_without_a_pending_login_is_refused(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        _, secret, _ = enrolled
        response = APIClient().post(f"{BASE}/mfa/verify/", {"code": _code(secret)})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "MFA_SESSION_EXPIRED"

    def test_pending_login_expires(
        self,
        enrolled: tuple[User, str, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user, secret, _ = enrolled
        client = APIClient()
        _login(client, user)
        real = __import__("time").time()
        monkeypatch.setattr(
            "apps.identity.api.time.time", lambda: real + mfa.PENDING_TTL_SECONDS + 5
        )
        response = client.post(f"{BASE}/mfa/verify/", {"code": _code(secret)})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "MFA_SESSION_EXPIRED"

    def test_exactly_one_of_code_or_recovery_code(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, secret, codes = enrolled
        client = APIClient()
        _login(client, user)
        assert client.post(f"{BASE}/mfa/verify/", {}).status_code == 400
        both = client.post(
            f"{BASE}/mfa/verify/", {"code": _code(secret), "recovery_code": codes[0]}
        )
        assert both.status_code == 400


class TestReplayProtection:
    def test_a_used_code_cannot_sign_in_again(
        self, enrolled: tuple[User, str, list[str]], frozen_step: list[int]
    ) -> None:
        user, secret, _ = enrolled
        code = _code(secret)
        first = APIClient()
        _login(first, user)
        assert first.post(f"{BASE}/mfa/verify/", {"code": code}).status_code == 200

        # An observer replays the very same code in the same time window.
        second = APIClient()
        _login(second, user)
        replay = second.post(f"{BASE}/mfa/verify/", {"code": code})
        assert replay.status_code == 401
        assert second.get(f"{BASE}/me/").status_code in (401, 403)

    def test_an_older_step_is_rejected_after_a_newer_one_was_accepted(
        self, enrolled: tuple[User, str, list[str]], frozen_step: list[int]
    ) -> None:
        user, secret, _ = enrolled
        device = TOTPDevice.objects.get(user=user)
        older = _code(secret, frozen_step[0] - 1)
        newer = _code(secret, frozen_step[0] + 1)
        assert mfa.verify_totp(device, newer) is True
        assert mfa.verify_totp(device, older) is False

    def test_concurrent_use_of_one_code_succeeds_once(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        """The step is advanced with a conditional UPDATE, so two requests that
        both passed the code check cannot both be accepted."""
        user, secret, _ = enrolled
        stale_a = TOTPDevice.objects.get(user=user)
        stale_b = TOTPDevice.objects.get(user=user)  # both read last_used_step before either wrote
        code = _code(secret)
        assert mfa.verify_totp(stale_a, code) is True
        assert mfa.verify_totp(stale_b, code) is False


class TestBruteForce:
    def test_pending_login_dies_after_a_few_wrong_codes(
        self, enrolled: tuple[User, str, list[str]], relaxed_throttle: None
    ) -> None:
        user, secret, _ = enrolled
        client = APIClient()
        _login(client, user)
        statuses = [
            client.post(f"{BASE}/mfa/verify/", {"code": "000000"}).status_code
            for _ in range(mfa.PENDING_MAX_FAILURES)
        ]
        assert statuses[-1] == 401
        # Even the right code no longer works on that attempt: log in again.
        late = client.post(f"{BASE}/mfa/verify/", {"code": _code(secret)})
        assert late.json()["error"]["code"] == "MFA_SESSION_EXPIRED"

    def test_account_lockout_survives_restarting_the_login(
        self, enrolled: tuple[User, str, list[str]], relaxed_throttle: None
    ) -> None:
        """An attacker who knows the password can restart the login after each
        batch of failures (fresh session, possibly another IP); the counter is
        per ACCOUNT, so the budget does not reset."""
        user, secret, _ = enrolled
        failures = 0
        while failures < mfa.LOCKOUT_MAX_FAILURES:
            client = APIClient()
            _login(client, user)
            for _ in range(mfa.PENDING_MAX_FAILURES):
                if failures >= mfa.LOCKOUT_MAX_FAILURES:
                    break
                client.post(f"{BASE}/mfa/verify/", {"code": "000000"})
                failures += 1

        assert mfa.is_locked_out(user)
        victim = APIClient()
        login = _login(victim, user)
        assert login.status_code == 429
        assert login.json()["error"]["code"] == "MFA_LOCKED_OUT"
        # And the correct code is refused too while locked out.
        assert victim.post(f"{BASE}/mfa/verify/", {"code": _code(secret)}).status_code in (401, 429)
        assert victim.get(f"{BASE}/me/").status_code in (401, 403)

    def test_lockout_clears_after_a_successful_login(
        self, enrolled: tuple[User, str, list[str]], relaxed_throttle: None
    ) -> None:
        user, secret, _ = enrolled
        client = APIClient()
        _login(client, user)
        for _ in range(3):
            client.post(f"{BASE}/mfa/verify/", {"code": "000000"})
        assert client.post(f"{BASE}/mfa/verify/", {"code": _code(secret)}).status_code == 200
        assert not mfa.is_locked_out(user)
        assert int(__import__("django").core.cache.cache.get(mfa._fail_key(user), 0)) == 0


class TestRecoveryCodes:
    def test_recovery_code_signs_in_exactly_once(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, codes = enrolled
        client = APIClient()
        _login(client, user)
        ok = client.post(f"{BASE}/mfa/verify/", {"recovery_code": codes[0]})
        assert ok.status_code == 200
        assert ok.json()["recovery_codes_remaining"] == mfa.RECOVERY_CODE_COUNT - 1
        assert AuditLog.objects.filter(action="auth.mfa_recovery_code_used").exists()

        again = APIClient()
        _login(again, user)
        assert again.post(f"{BASE}/mfa/verify/", {"recovery_code": codes[0]}).status_code == 401

    def test_format_is_forgiving_but_codes_are_not_guessable(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, codes = enrolled
        client = APIClient()
        _login(client, user)
        messy = " " + codes[1].upper().replace("-", " ") + " "
        assert client.post(f"{BASE}/mfa/verify/", {"recovery_code": messy}).status_code == 200
        other = APIClient()
        _login(other, user)
        assert (
            other.post(f"{BASE}/mfa/verify/", {"recovery_code": "aaaaa-aaaaa"}).status_code == 401
        )

    def test_one_users_code_does_not_work_for_another(
        self, enrolled: tuple[User, str, list[str]], frozen_step: list[int]
    ) -> None:
        _, _, codes = enrolled
        other = factories.make_user()
        _enrol(other)
        client = APIClient()
        _login(client, other)
        assert client.post(f"{BASE}/mfa/verify/", {"recovery_code": codes[0]}).status_code == 401

    def test_regenerate_needs_password_and_totp_and_invalidates_old_codes(
        self, enrolled: tuple[User, str, list[str]], frozen_step: list[int]
    ) -> None:
        user, secret, old = enrolled
        client = APIClient()
        client.force_authenticate(user)
        url = f"{BASE}/mfa/recovery-codes/"
        assert (
            client.post(url, {"password": "nope-nope-nope", "code": _code(secret)}).status_code
            == 403
        )
        assert client.post(url, {"password": PASSWORD, "code": "000000"}).status_code == 401
        # A recovery code is NOT accepted here.
        assert client.post(url, {"password": PASSWORD, "recovery_code": old[0]}).status_code == 401

        frozen_step[0] += 1
        fresh = client.post(url, {"password": PASSWORD, "code": _code(secret)})
        assert fresh.status_code == 200
        new = fresh.json()["recovery_codes"]
        assert set(new).isdisjoint(old)
        assert RecoveryCode.objects.filter(user=user).count() == mfa.RECOVERY_CODE_COUNT

        login = APIClient()
        _login(login, user)
        assert login.post(f"{BASE}/mfa/verify/", {"recovery_code": old[2]}).status_code == 401


class TestDisable:
    def test_disable_requires_password_and_a_valid_factor(
        self, enrolled: tuple[User, str, list[str]], frozen_step: list[int]
    ) -> None:
        user, secret, _ = enrolled
        client = APIClient()
        client.force_authenticate(user)
        url = f"{BASE}/mfa/disable/"
        assert (
            client.post(url, {"password": "wrong-wrong-wrong", "code": _code(secret)}).status_code
            == 403
        )
        assert client.post(url, {"password": PASSWORD, "code": "000000"}).status_code == 401
        assert mfa.user_has_mfa(User.objects.get(pk=user.pk))

        frozen_step[0] += 1
        assert client.post(url, {"password": PASSWORD, "code": _code(secret)}).status_code == 204
        assert not TOTPDevice.objects.filter(user=user).exists()
        assert not RecoveryCode.objects.filter(user=user).exists()
        assert AuditLog.objects.filter(action="auth.mfa_disabled").exists()

        plain = _login(APIClient(), user)
        assert plain.status_code == 200 and "mfa_required" not in plain.json()

    def test_a_recovery_code_can_disable_when_the_phone_is_lost(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, codes = enrolled
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(
            f"{BASE}/mfa/disable/", {"password": PASSWORD, "recovery_code": codes[0]}
        )
        assert response.status_code == 204


class TestBypassAttempts:
    """The second factor must hold even when the attacker never touches the
    MFA endpoints."""

    def test_session_created_without_mfa_is_refused_by_the_api(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, _ = enrolled
        client = APIClient()
        client.force_login(user)  # what the Django admin login does: no mfa marker
        response = client.get(f"{BASE}/me/")
        assert response.status_code in (401, 403)
        assert response.json()["error"]["code"] == "MFA_REQUIRED"

    def test_session_created_without_mfa_is_refused_by_the_admin_site(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, _ = enrolled
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        client = APIClient()
        client.force_login(user)
        assert client.get("/admin/").status_code == 403

    def test_users_without_mfa_are_unaffected(self, owner: Any) -> None:
        client = APIClient()
        client.force_login(owner)
        assert client.get(f"{BASE}/me/").status_code == 200

    def test_enabling_mfa_signs_out_the_users_other_sessions(self, owner: Any) -> None:
        old_session = APIClient()
        assert _login(old_session, owner).status_code == 200
        assert old_session.get(f"{BASE}/me/").status_code == 200

        current = APIClient()
        _login(current, owner)
        secret = current.post(f"{BASE}/mfa/setup/", {"password": PASSWORD}).json()["secret"]
        assert current.post(f"{BASE}/mfa/confirm/", {"code": _code(secret)}).status_code == 200

        assert current.get(f"{BASE}/me/").status_code == 200
        assert old_session.get(f"{BASE}/me/").status_code in (401, 403)

    def test_platform_staff_requirement_is_off_by_default_and_enforceable(
        self, settings: Any
    ) -> None:
        staff = factories.make_user(is_staff=True)
        client = APIClient()
        client.force_authenticate(staff)
        assert client.get("/api/v1/platform/organizations/").status_code == 200

        settings.MFA_REQUIRED_FOR_STAFF = True
        blocked = client.get("/api/v1/platform/organizations/")
        assert blocked.status_code == 403
        assert (
            blocked.json()["error"]["code"] == "MFA_SETUP_REQUIRED"
            or "two-factor" in str(blocked.json()).lower()
        )

        _enrol(staff)
        assert client.get("/api/v1/platform/organizations/").status_code == 200

    @pytest.mark.django_db(transaction=True)
    def test_websocket_refuses_a_session_that_skipped_mfa(
        self, enrolled: tuple[User, str, list[str]]
    ) -> None:
        user, _, _ = enrolled
        org = user.memberships.first().organization
        business = factories.make_business(org)
        app = URLRouter(websocket_urlpatterns)

        async def attempt(session: dict[str, Any]) -> tuple[bool, Any]:
            comm = WebsocketCommunicator(
                app, f"/ws/v1/realtime/?organization={org.pk}&business={business.pk}"
            )
            comm.scope["user"] = user
            comm.scope["session"] = session
            result = await comm.connect()
            await comm.disconnect()
            return result

        refused, code = async_to_sync(attempt)({})
        assert not refused and code == 4401
        accepted, _ = async_to_sync(attempt)({mfa.SESSION_MFA_VERIFIED: mfa.mfa_stamp(user)})
        stale, _ = async_to_sync(attempt)({mfa.SESSION_MFA_VERIFIED: "1999-01-01T00:00:00+00:00"})
        assert not stale
        assert accepted
