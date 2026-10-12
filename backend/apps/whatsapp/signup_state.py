"""One-time ``state`` for Embedded Signup (CSRF / replay protection).

The browser asks for a signup session *before* opening Meta's dialog; the
completion call must hand the same value back. The value is random, bound to
the user, organization and business that asked for it, single-use and short
lived, so a forged, replayed, cross-user or cross-tenant completion is refused
before any Meta call is made. Stored in the cache, never in the database or in
logs; it is not a credential and grants nothing by itself.
"""

import secrets
from dataclasses import dataclass
from typing import Any

from django.core.cache import cache

from core.exceptions import DomainError

TTL_SECONDS = 15 * 60
_PREFIX = "wa-signup-state:"


class InvalidSignupStateError(DomainError):
    code = "SIGNUP_STATE_INVALID"
    default_message = (
        "This connection attempt is no longer valid. Start the connection again from the beginning."
    )


@dataclass(frozen=True)
class SignupSession:
    state: str
    expires_in: int


def issue_state(*, user: Any, organization: Any, business: Any) -> SignupSession:
    state = secrets.token_urlsafe(24)
    cache.set(
        _PREFIX + state,
        {"user": str(user.pk), "org": str(organization.pk), "business": str(business.pk)},
        TTL_SECONDS,
    )
    return SignupSession(state=state, expires_in=TTL_SECONDS)


def consume_state(*, state: str, user: Any, organization: Any, business: Any) -> None:
    """Validate and burn ``state``. Burned even when it does not match, so a
    guess or a stolen value cannot be retried."""
    key = _PREFIX + (state or "")
    stored = cache.get(key) if state else None
    if state:
        cache.delete(key)
    expected = {"user": str(user.pk), "org": str(organization.pk), "business": str(business.pk)}
    if stored != expected:
        raise InvalidSignupStateError()
