from typing import Any

from django.conf import settings
from django.db import models

from core.models import BaseModel


class AuditLogQuerySet(models.QuerySet["AuditLog"]):
    def update(self, **kwargs: Any) -> int:
        raise TypeError("audit logs are append-only")


class AuditLog(BaseModel):
    """Append-only record of security- and business-relevant actions.

    ``organization`` is nullable for platform-level events (e.g. a failed
    login that never resolved to a tenant). Secrets are scrubbed from
    ``metadata`` before insert (apps.audit.services).
    """

    class ActorType(models.TextChoices):
        USER = "user", "User"
        SYSTEM = "system", "System"
        PROVIDER = "provider", "Provider"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=100, blank=True)
    resource_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    request_id = models.CharField(max_length=128, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Audit screen: newest first within a tenant.
            models.Index(fields=["organization", "-created_at"], name="audit_org_created_idx"),
            # "What happened to this resource?"
            models.Index(
                fields=["organization", "resource_type", "resource_id"],
                name="audit_org_resource_idx",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise TypeError("audit logs are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("audit logs are append-only")
