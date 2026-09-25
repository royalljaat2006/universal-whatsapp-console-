"""Tenant scoping primitives.

Strategy (ADR-001): shared database, shared schema, ``organization_id`` on
every tenant-owned row. Isolation is enforced server-side in three layers:

1. Models inherit ``TenantScopedModel``; its manager offers
   ``for_organization(org)`` — the only sanctioned way for request-path code
   to read tenant data.
2. API views inherit ``OrganizationScopedMixin`` (apps.organizations.api),
   which resolves the organization from the authenticated membership and
   builds every queryset through ``for_organization``.
3. Services assert cross-entity consistency with ``ensure_same_organization``
   (e.g. a message's conversation and phone number share its tenant).

Unscoped ``.objects.all()`` is reserved for system context (webhook
processing, workers, migrations), where the tenant is derived from provider
identifiers rather than a user.
"""

from typing import Any, Self

from django.db import models

from core.models import BaseModel


class CrossTenantReferenceError(ValueError):
    """Raised when entities from different organizations are combined."""


class TenantQuerySet(models.QuerySet[Any]):
    def for_organization(self, organization: Any) -> Self:
        if organization is None:
            raise ValueError("for_organization() requires an organization")
        org_id = getattr(organization, "pk", organization)
        return self.filter(organization_id=org_id)


class TenantScopedModel(BaseModel):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="+",
        editable=False,
    )

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True


def ensure_same_organization(*entities: Any) -> None:
    org_ids = {getattr(e, "organization_id", None) for e in entities if e is not None}
    if len(org_ids) > 1 or None in org_ids:
        raise CrossTenantReferenceError(
            "entities belong to different organizations: "
            + ", ".join(type(e).__name__ for e in entities if e is not None)
        )
