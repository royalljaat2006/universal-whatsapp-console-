"""Organization, membership and authorization services (public interface)."""

from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.audit import actions
from apps.audit.services import record as audit
from apps.organizations.models import Membership, Organization, Permission, Role
from apps.organizations.permissions import DEFAULT_ROLES, OWNER_ROLE_KEY, PERMISSIONS
from core.exceptions import ConflictError, NotFoundError


def sync_permission_catalogue() -> None:
    """Idempotently mirror permissions.PERMISSIONS into the Permission table."""
    for code, description in PERMISSIONS.items():
        Permission.objects.update_or_create(code=code, defaults={"description": description})


def seed_default_roles(organization: Organization) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for key, spec in DEFAULT_ROLES.items():
        role, _ = Role.objects.get_or_create(
            organization=organization,
            key=key,
            defaults={
                "name": spec["name"],
                "description": spec["description"],
                "is_system": True,
            },
        )
        role.permissions.set(Permission.objects.filter(code__in=spec["permissions"]))
        roles[key] = role
    return roles


@transaction.atomic
def create_organization(*, name: str, owner: Any, slug: str | None = None) -> Organization:
    slug = slug or slugify(name)[:80]
    if Organization.objects.filter(slug=slug).exists():
        raise ConflictError(f"organization slug '{slug}' is taken")
    sync_permission_catalogue()
    organization = Organization.objects.create(name=name, slug=slug)
    roles = seed_default_roles(organization)
    Membership.objects.create(
        organization=organization,
        user=owner,
        role=roles[OWNER_ROLE_KEY],
        status=Membership.Status.ACTIVE,
        joined_at=timezone.now(),
    )
    audit(
        actions.ORGANIZATION_CREATED, organization=organization, actor=owner, resource=organization
    )
    return organization


ALL_BUSINESS_ROLES = frozenset({"owner", "admin"})


@transaction.atomic
def add_member(
    *,
    organization: Organization,
    user: Any,
    role_key: str,
    added_by: Any = None,
    businesses: list[Any] | None = None,
) -> Membership:
    """Add a member. Owners/admins see every business unless ``businesses`` is
    given; every other role sees only the ``businesses`` granted here (none if
    omitted), so least privilege is the default."""
    from apps.businesses.services import grant_business_access

    role = Role.objects.for_organization(organization).filter(key=role_key).first()
    if role is None:
        raise NotFoundError(f"role '{role_key}' does not exist in this organization")
    scope = (
        Membership.BusinessScope.ALL
        if role_key in ALL_BUSINESS_ROLES and businesses is None
        else Membership.BusinessScope.SELECTED
    )
    membership, created = Membership.objects.get_or_create(
        organization=organization,
        user=user,
        defaults={
            "role": role,
            "status": Membership.Status.ACTIVE,
            "business_scope": scope,
            "invited_by": added_by,
            "joined_at": timezone.now(),
        },
    )
    if not created:
        raise ConflictError("user is already a member of this organization")
    for business in businesses or []:
        grant_business_access(membership, business, actor=added_by)
    audit(
        actions.MEMBER_ADDED,
        organization=organization,
        actor=added_by,
        resource=membership,
        metadata={
            "role": role_key,
            "user_id": str(user.pk),
            "business_scope": scope,
            "businesses": [str(b.pk) for b in businesses or []],
        },
    )
    return membership


def get_active_membership(user: Any, organization_id: Any) -> Membership | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return (
        Membership.objects.select_related("organization", "role")
        .filter(
            user=user,
            organization_id=organization_id,
            status=Membership.Status.ACTIVE,
            organization__status=Organization.Status.ACTIVE,
        )
        .first()
    )


def permission_codes(membership: Membership) -> frozenset[str]:
    """Permission codes granted by the membership's role (memoised per instance)."""
    cached = getattr(membership, "_permission_codes", None)
    if cached is None:
        cached = frozenset(membership.role.permissions.values_list("code", flat=True))
        membership._permission_codes = cached  # type: ignore[attr-defined]
    return cached


def has_permission(membership: Membership | None, code: str) -> bool:
    if code not in PERMISSIONS:
        raise ValueError(f"unknown permission '{code}'")
    return membership is not None and code in permission_codes(membership)
