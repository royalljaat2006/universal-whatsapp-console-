"""API key lifecycle. Public interface for this app."""

import hashlib
import hmac
import secrets
from typing import Any

from django.utils import timezone

from apps.apikeys.models import ApiKey
from apps.audit import actions
from apps.audit.services import record as audit
from apps.organizations.models import Organization
from apps.organizations.permissions import PERMISSIONS
from core.exceptions import ConflictError, DomainError

TOKEN_PREFIX = "wcapi_"
# "wcapi_" + 12 random chars is the stored, plaintext lookup key; the rest of
# the raw token (part of a 32-byte secrets.token_urlsafe) is never stored.
PREFIX_LENGTH = len(TOKEN_PREFIX) + 12


class InvalidScopeError(DomainError):
    code = "INVALID_SCOPE"
    default_message = "One or more requested scopes are not valid permissions."


def _hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def create_api_key(
    *,
    organization: Organization,
    created_by: Any,
    name: str,
    scopes: list[str],
    expires_at: Any = None,
) -> tuple[ApiKey, str]:
    """Create a key. Returns it alongside the raw token — shown exactly once,
    exactly like an invitation token; only its hash is ever persisted."""
    unknown = set(scopes) - set(PERMISSIONS)
    if unknown:
        raise InvalidScopeError(f"Unknown permission codes: {sorted(unknown)}")
    raw_key = TOKEN_PREFIX + secrets.token_urlsafe(32)
    prefix = raw_key[:PREFIX_LENGTH]
    api_key = ApiKey.objects.create(
        organization=organization,
        created_by=created_by,
        name=name,
        key_prefix=prefix,
        key_hash=_hash(raw_key),
        scopes=sorted(set(scopes)),
        expires_at=expires_at,
    )
    audit(
        actions.API_KEY_CREATED,
        organization=organization,
        actor=created_by,
        resource=api_key,
        metadata={"name": name, "scopes": api_key.scopes},
    )
    return api_key, raw_key


def revoke_api_key(*, api_key: ApiKey, actor: Any) -> ApiKey:
    if api_key.revoked_at is not None:
        raise ConflictError("This API key is already revoked.")
    api_key.revoked_at = timezone.now()
    api_key.save(update_fields=["revoked_at", "updated_at"])
    audit(
        actions.API_KEY_REVOKED,
        organization=api_key.organization,
        actor=actor,
        resource=api_key,
        metadata={"name": api_key.name},
    )
    return api_key


def authenticate_api_key(raw_key: str) -> ApiKey | None:
    """Constant-time verified lookup. Returns None (never raises) for any
    invalid, unknown, revoked or expired key — the caller decides how to
    surface that as an auth failure."""
    if not raw_key.startswith(TOKEN_PREFIX):
        return None
    prefix = raw_key[:PREFIX_LENGTH]
    api_key = ApiKey.objects.select_related("created_by").filter(key_prefix=prefix).first()
    if api_key is None:
        return None
    if not hmac.compare_digest(api_key.key_hash, _hash(raw_key)):
        return None
    if not api_key.is_active:
        return None
    if api_key.created_by is None or not api_key.created_by.is_active:
        return None
    ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
    return api_key
