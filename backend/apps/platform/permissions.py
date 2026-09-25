from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request


class IsPlatformAdmin(BasePermission):
    """Platform-level (cross-tenant) access. Reuses Django's own ``is_staff``
    — already the platform-operator gate for the admin site and
    ``/health/dependencies`` — rather than adding a second, parallel concept
    of "admin"."""

    def has_permission(self, request: Request, view: Any) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)
