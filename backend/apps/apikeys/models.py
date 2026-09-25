"""Tenant-scoped API keys for programmatic access.

An ApiKey authenticates a request AS its ``created_by`` user — that user's
active membership and role still govern what is possible (the same tenancy
and permission machinery as a session), and ``scopes`` narrows it further: a
key can only do what its scopes list AND its creator's role both allow. See
apps.apikeys.authentication and the scope check in
apps.organizations.api.scoping.HasOrganizationPermission.

Only a SHA-256 hash of the key is ever stored (services.create_api_key
returns the raw value exactly once, the same discipline as an invitation
token) — this file never sees or stores a usable secret.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.organizations.tenancy import TenantScopedModel


class ApiKey(TenantScopedModel):
    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    # The first characters of the raw key, stored in plain text so a lookup
    # doesn't require hashing every stored key to find a match; long enough
    # that collisions are not a practical concern (see apikeys.services).
    key_prefix = models.CharField(max_length=24, unique=True, editable=False)
    key_hash = models.CharField(max_length=64, editable=False)
    # Subset of apps.organizations.permissions.PERMISSIONS this key may use.
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "revoked_at"], name="apikey_org_revoked_idx")
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.key_prefix}…)"

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()
