"""TOTP (RFC 6238) multi-factor authentication for the first-party console.

Implemented on the standard library (HMAC-SHA1, 6 digits, 30 s step - the
parameters every authenticator app supports) so there is no extra dependency
to audit. Secrets are encrypted at rest with core.crypto. The functions here
hold the security-relevant decisions; apps.identity.api is only transport.

Session model: a successful password check for a user who has a confirmed
device does NOT log them in. It stores a short-lived *pending* marker in the
(still anonymous) session; only a valid TOTP or recovery code promotes it to a
real login and sets ``SESSION_MFA_VERIFIED``. Session authentication
(apps.identity.authentication) and the admin guard reject a session for such
a user that lacks the verified marker, so a login path that skips this flow
(Django admin) cannot be used to bypass the second factor.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Final
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.identity.models import RecoveryCode, TOTPDevice, User
from core import crypto

DIGITS: Final = 6
STEP_SECONDS: Final = 30
# Accept the previous and next step as well, to tolerate clock drift between
# the phone and the server (RFC 6238 section 5.2 recommends at most one step).
WINDOW: Final = 1
SECRET_BYTES: Final = 20  # 160 bits, the RFC 4226 recommendation
RECOVERY_CODE_COUNT: Final = 10
_RECOVERY_ALPHABET: Final = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/i/l
_RECOVERY_LEN: Final = 10  # ~49.5 bits per code

SESSION_PENDING_USER: Final = "mfa_pending_user_id"
SESSION_PENDING_AT: Final = "mfa_pending_at"
SESSION_PENDING_FAILS: Final = "mfa_pending_fails"
SESSION_MFA_VERIFIED: Final = "mfa_verified"

PENDING_TTL_SECONDS: Final = 5 * 60
PENDING_MAX_FAILURES: Final = 5
# Per-ACCOUNT lockout, independent of the session and of the client IP: an
# attacker who restarts the login (new session) or rotates IPs still burns the
# same counter.
LOCKOUT_MAX_FAILURES: Final = 10
LOCKOUT_WINDOW_SECONDS: Final = 15 * 60


# --------------------------------------------------------------------- TOTP


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _b32decode(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)


def _hotp(key: bytes, counter: int) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**DIGITS)
    return f"{code:0{DIGITS}d}"


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def totp_code(secret: str, step: int) -> str:
    return _hotp(_b32decode(secret), step)


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The time-step ``code`` is valid for within the drift window, else None.
    Compares every candidate in constant time (no early exit on a match)."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    key = _b32decode(secret)
    base = current_step(now)
    found: int | None = None
    for step in range(base - WINDOW, base + WINDOW + 1):
        if hmac.compare_digest(_hotp(key, step), code):
            found = step
    return found


def provisioning_uri(user: User, secret: str) -> str:
    issuer = getattr(settings, "MFA_ISSUER", "WhatsApp Console")
    label = quote(f"{issuer}:{user.email}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


# ----------------------------------------------------------- recovery codes


def hash_recovery_code(code: str) -> str:
    """Keyed hash (HMAC-SHA256 with a key derived from SECRET_KEY). The codes
    carry ~49 bits of entropy and are single-use, so a fast keyed hash is
    appropriate; the key means a leaked table alone cannot be brute-forced
    offline."""
    normalised = code.strip().lower().replace("-", "").replace(" ", "")
    key = hashlib.sha256(b"identity.recovery-code|" + settings.SECRET_KEY.encode()).digest()
    return hmac.new(key, normalised.encode(), hashlib.sha256).hexdigest()


def _new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LEN))
    return f"{raw[:5]}-{raw[5:]}"


@transaction.atomic
def regenerate_recovery_codes(user: User) -> list[str]:
    """Replace ALL existing codes with a fresh set; returns the plaintext once."""
    RecoveryCode.objects.filter(user=user).delete()
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    RecoveryCode.objects.bulk_create(
        [RecoveryCode(user=user, code_hash=hash_recovery_code(c)) for c in codes]
    )
    return codes


def consume_recovery_code(user: User, code: str) -> bool:
    """Atomically marks the code used; a second use (or a race) updates 0 rows."""
    updated = RecoveryCode.objects.filter(
        user=user, code_hash=hash_recovery_code(code), used_at__isnull=True
    ).update(used_at=timezone.now())
    return updated == 1


def recovery_codes_remaining(user: User) -> int:
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


# ----------------------------------------------------------------- devices


def confirmed_device(user: User) -> TOTPDevice | None:
    return TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).first()


_UNSET: Final = object()


def mfa_stamp(user: User) -> str | None:
    """Identifies the user's CURRENT second-factor enrolment (the confirmation
    time of the device), or None when MFA is off. A session proves it passed
    MFA by carrying exactly this value, so a session opened before the user
    enrolled - or against a device they later removed and replaced - stops
    being valid the moment the enrolment changes. Cached on the user object
    for the duration of a request."""
    cached = getattr(user, "_mfa_stamp", _UNSET)
    if cached is _UNSET:
        confirmed_at = (
            TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False)
            .values_list("confirmed_at", flat=True)
            .first()
        )
        cached = confirmed_at.isoformat() if confirmed_at else None
        user._mfa_stamp = cached  # type: ignore[attr-defined]
    return cached  # type: ignore[return-value]


def forget_cached_stamp(user: User) -> None:
    if hasattr(user, "_mfa_stamp"):
        del user._mfa_stamp


def user_has_mfa(user: User) -> bool:
    return mfa_stamp(user) is not None


class AlreadyEnrolledError(Exception):
    pass


def begin_enrollment(user: User) -> tuple[str, str]:
    """(Re)start enrolment: a fresh unconfirmed secret. Refuses to replace a
    confirmed device - disable it first (which needs password AND a code)."""
    if confirmed_device(user) is not None:
        raise AlreadyEnrolledError
    secret = generate_secret()
    TOTPDevice.objects.update_or_create(
        user=user,
        defaults={
            "encrypted_secret": crypto.encrypt(secret),
            "confirmed_at": None,
            "last_used_step": 0,
        },
    )
    return secret, provisioning_uri(user, secret)


def verify_totp(device: TOTPDevice, code: str, now: float | None = None) -> bool:
    """Valid code for a step later than any accepted before (replay-proof).
    The step is advanced with a conditional UPDATE so two concurrent requests
    presenting the same code cannot both succeed."""
    step = matching_step(crypto.decrypt(device.encrypted_secret), code, now)
    if step is None:
        return False
    advanced = TOTPDevice.objects.filter(pk=device.pk, last_used_step__lt=step).update(
        last_used_step=step
    )
    return advanced == 1


@transaction.atomic
def confirm_enrollment(user: User, code: str) -> list[str] | None:
    """Confirms a pending device with a valid code and issues recovery codes
    (plaintext, once). None when there is no pending device or the code is bad."""
    device = TOTPDevice.objects.select_for_update().filter(user=user).first()
    if device is None or device.confirmed_at is not None:
        return None
    if not verify_totp(device, code):
        return None
    TOTPDevice.objects.filter(pk=device.pk).update(confirmed_at=timezone.now())
    return regenerate_recovery_codes(user)


def disable(user: User) -> None:
    TOTPDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()


# ------------------------------------------------- brute-force protection


def _fail_key(user: User) -> str:
    return f"mfa:fails:{user.pk}"


def is_locked_out(user: User) -> bool:
    return int(cache.get(_fail_key(user), 0)) >= LOCKOUT_MAX_FAILURES


def register_failure(user: User) -> int:
    key = _fail_key(user)
    cache.add(key, 0, LOCKOUT_WINDOW_SECONDS)
    try:
        return int(cache.incr(key))
    except ValueError:  # expired between add and incr
        cache.set(key, 1, LOCKOUT_WINDOW_SECONDS)
        return 1


def clear_failures(user: User) -> None:
    cache.delete(_fail_key(user))


def check_second_factor(user: User, *, code: str = "", recovery_code: str = "") -> str | None:
    """Validates ONE second factor for ``user`` (who has a confirmed device).
    Returns "totp" / "recovery" on success, else None. Every failure counts
    against the per-account lockout, and once locked nothing is evaluated at
    all (a correct code during lockout is refused too)."""
    if is_locked_out(user):
        return None
    device = confirmed_device(user)
    if device is None:
        return None
    method: str | None = None
    if code and verify_totp(device, code):
        method = "totp"
    elif recovery_code and consume_recovery_code(user, recovery_code):
        method = "recovery"
    if method is None:
        register_failure(user)
        return None
    clear_failures(user)
    return method
