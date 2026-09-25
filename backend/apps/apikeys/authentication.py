"""``Authorization: Bearer <key>`` authentication.

On success, request.user is the key's creator — the SAME session-equivalent
identity that apps.organizations.api.scoping.resolve_membership already knows
how to turn into a membership via the usual X-Organization-ID header, so
tenant scoping and role permissions are enforced completely unchanged. The
one addition a key needs is its own ``scopes``, checked in
HasOrganizationPermission alongside the role permission (see that module).
"""

from typing import Any

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from apps.apikeys.models import ApiKey
from apps.apikeys.services import authenticate_api_key

KEYWORD = "Bearer"


class ApiKeyAuthentication(BaseAuthentication):
    def authenticate(self, request: Request) -> tuple[Any, ApiKey] | None:
        header = request.META.get("HTTP_AUTHORIZATION", "")
        prefix, _, raw_key = header.partition(" ")
        if prefix != KEYWORD or not raw_key:
            return None
        api_key = authenticate_api_key(raw_key)
        if api_key is None:
            raise AuthenticationFailed("Invalid, revoked or expired API key.")
        return (api_key.created_by, api_key)

    def authenticate_header(self, request: Request) -> str:
        return KEYWORD
