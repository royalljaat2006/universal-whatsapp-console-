from typing import Any

from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.identity.mfa import user_has_mfa


class IsPlatformAdmin(BasePermission):
    """Platform-level (cross-tenant) access. Reuses Django's own ``is_staff``
    — already the platform-operator gate for the admin site and
    ``/health/dependencies`` — rather than adding a second, parallel concept
    of "admin"."""

    def has_permission(self, request: Request, view: Any) -> bool:
        allowed = bool(request.user and request.user.is_authenticated and request.user.is_staff)
        if allowed and settings.MFA_REQUIRED_FOR_STAFF and not user_has_mfa(request.user):  # type: ignore[arg-type]  # is_authenticated checked above
            # Distinct code so the console can send the operator to enrol.
            raise PermissionDenied(
                "Platform access requires two-factor authentication. "
                "Enable it under Settings > Security first.",
                code="mfa_setup_required",
            )
        return allowed
