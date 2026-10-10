"""Permissions specific to account-security endpoints."""

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.apikeys.models import ApiKey


class InteractiveSessionOnly(BasePermission):
    """Refuses requests authenticated by an API key.

    An API key authenticates as its creator, so without this a leaked key could
    call any endpoint that merely checks ``IsAuthenticated`` - including the
    ones that confirm the account password (account deletion, two-step
    verification management) and so use them as a password oracle, or manage
    the creator's security settings. Keys are for programmatic access to
    tenant data; changing who can sign in needs a person in a real session."""

    message = "This action needs an interactive sign-in, not an API key."

    def has_permission(self, request: Request, view: Any) -> bool:
        return not isinstance(request.auth, ApiKey)
