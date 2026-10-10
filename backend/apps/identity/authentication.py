"""Session authentication that honours the second factor.

DRF's stock SessionAuthentication accepts any valid session. A session can
come into existence without passing through apps.identity.api.LoginView - the
Django admin login does exactly that - so for a user who has MFA enabled the
session must additionally carry the ``mfa_verified`` marker that only a
completed TOTP / recovery-code check sets. Without this, MFA would be a
cosmetic step in one login endpoint and the API (and /admin/, and the
WebSocket) could still be reached with the password alone.

Users who have not enrolled are unaffected, so enabling the feature locks
nobody out.
"""

from django.http import HttpRequest, HttpResponse
from rest_framework.authentication import SessionAuthentication
from rest_framework.request import Request

from apps.identity.mfa import SESSION_MFA_VERIFIED, mfa_stamp
from core.exceptions import DomainError


class MfaRequiredError(DomainError):
    code = "MFA_REQUIRED"
    status_code = 401
    default_message = "Two-factor verification is required. Sign in again to complete it."


def session_satisfies_mfa(user: object, session: object) -> bool:
    """True when ``user`` needs no second factor, or this session carries the
    stamp of their CURRENT enrolment (set only by a completed MFA check)."""
    if not getattr(user, "is_authenticated", False):
        return True
    stamp = mfa_stamp(user)  # type: ignore[arg-type]
    if stamp is None:
        return True
    return getattr(session, "get", lambda _k: None)(SESSION_MFA_VERIFIED) == stamp


class MFASessionAuthentication(SessionAuthentication):
    def authenticate(self, request: Request) -> tuple[object, None] | None:
        result = super().authenticate(request)
        if result is None:
            return None
        user, _ = result
        if not session_satisfies_mfa(user, request._request.session):
            raise MfaRequiredError()
        return result


class AdminMFAGuardMiddleware:
    """Same rule for the Django admin site, which authenticates through
    Django's own session login rather than DRF."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path.startswith("/admin/") and not session_satisfies_mfa(
            request.user, request.session
        ):
            return HttpResponse(
                "Two-factor verification required. Sign in through the console first.",
                status=403,
                content_type="text/plain",
            )
        return self.get_response(request)
