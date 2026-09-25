"""Business / workspace: a line of business inside an organization (e.g. Kiosk,
EPS). It owns WABAs, and through them phone numbers, contacts, conversations
and messages. Businesses are data, never code: any number can be created.

Access: a Membership either sees every business of its organization
(``Membership.business_scope = all``) or only those granted through
BusinessAccess rows. Owners and admins are ``all`` by default.
"""

from django.db import models

from apps.organizations.tenancy import TenantScopedModel


class Business(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    timezone = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"], name="business_org_slug_unique"
            ),
        ]
        ordering = ["name"]
        verbose_name_plural = "businesses"

    def __str__(self) -> str:
        return self.name


class BusinessAccess(TenantScopedModel):
    """Grants a membership with ``business_scope=selected`` access to one business."""

    membership = models.ForeignKey(
        "organizations.Membership", on_delete=models.CASCADE, related_name="business_access"
    )
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="access_grants")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "business"], name="business_access_unique"
            ),
        ]
