from django.conf import settings
from django.db import models

from apps.organizations.tenancy import TenantScopedModel
from core.models import BaseModel


class Organization(BaseModel):
    """Tenant root. Every tenant-owned row references exactly one organization."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    timezone = models.CharField(max_length=64, default="UTC")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE


class Permission(models.Model):
    """Global permission catalogue (synchronised from permissions.PERMISSIONS)."""

    code = models.CharField(max_length=64, primary_key=True)
    description = models.CharField(max_length=255)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class Role(TenantScopedModel):
    key = models.SlugField(max_length=64)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    is_system = models.BooleanField(
        default=False, help_text="Seeded default role; cannot be deleted."
    )
    permissions = models.ManyToManyField(Permission, related_name="roles", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "key"], name="role_org_key_unique"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.organization_id})"


class Membership(TenantScopedModel):
    """A user's access to one organization. Authorization always goes through
    an active membership — never through the User alone."""

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="memberships")

    class BusinessScope(models.TextChoices):
        ALL = "all", "All businesses"
        SELECTED = "selected", "Selected businesses only"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    # Which businesses (workspaces) of the organization this member can see.
    business_scope = models.CharField(
        max_length=16, choices=BusinessScope.choices, default=BusinessScope.ALL
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="membership_org_user_unique"
            ),
        ]
        indexes = [
            # "Which organizations can this user access?" on every request.
            models.Index(fields=["user", "status"], name="membership_user_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.organization_id}"


class Invitation(TenantScopedModel):
    """A pending invite for an email address to join the organization. The raw
    token is shown to the inviter exactly once (see services.create_invitation)
    and only its hash is ever stored — the same discipline as an API key."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"

    email = models.EmailField(max_length=254)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="invitations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    token_hash = models.CharField(max_length=64, editable=False)
    business_scope = models.CharField(
        max_length=16,
        choices=Membership.BusinessScope.choices,
        default=Membership.BusinessScope.ALL,
    )
    # Business ids granted on acceptance when business_scope=SELECTED (mirrors
    # add_member's `businesses` argument; kept as a plain id list rather than a
    # through-table since these are proposed grants, not yet real access).
    business_ids = models.JSONField(default=list, blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # One outstanding invite per email at a time; revoke-and-reinvite
            # is the path to change role/businesses before it's accepted.
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(status="pending"),
                name="invitation_org_email_pending_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="invitation_org_status_idx")
        ]

    def __str__(self) -> str:
        return f"{self.email} -> {self.organization_id} ({self.status})"
