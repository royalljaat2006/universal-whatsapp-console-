"""Organization, membership and authorization services (public interface)."""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status

from apps.audit import actions
from apps.audit.services import record as audit
from apps.organizations.models import Invitation, Membership, Organization, Permission, Role
from apps.organizations.permissions import DEFAULT_ROLES, OWNER_ROLE_KEY, PERMISSIONS
from core.exceptions import ConflictError, DomainError, NotFoundError

INVITATION_TTL_DAYS = 7


class InvitationInvalidError(DomainError):
    code = "INVITATION_INVALID"
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "This invitation link is invalid, expired or already used."


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


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


@transaction.atomic
def create_invitation(
    *,
    organization: Organization,
    email: str,
    role_key: str,
    invited_by: Any,
    businesses: list[Any] | None = None,
) -> tuple[Invitation, str]:
    """Create a pending invitation. Returns the raw token alongside it — the
    only time it is ever available; only its hash is persisted."""
    email = email.strip().lower()
    role = Role.objects.for_organization(organization).filter(key=role_key).first()
    if role is None:
        raise NotFoundError(f"role '{role_key}' does not exist in this organization")
    already_member = (
        Membership.objects.for_organization(organization)
        .filter(user__email__iexact=email, status=Membership.Status.ACTIVE)
        .exists()
    )
    if already_member:
        raise ConflictError("This person is already a member of the organization.")
    pending_exists = (
        Invitation.objects.for_organization(organization)
        .filter(email__iexact=email, status=Invitation.Status.PENDING)
        .exists()
    )
    if pending_exists:
        raise ConflictError("An invitation is already pending for this email.")
    scope = (
        Membership.BusinessScope.ALL
        if role_key in ALL_BUSINESS_ROLES and businesses is None
        else Membership.BusinessScope.SELECTED
    )
    raw_token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        organization=organization,
        email=email,
        role=role,
        token_hash=_hash_token(raw_token),
        business_scope=scope,
        business_ids=[str(b.pk) for b in businesses or []],
        invited_by=invited_by,
        expires_at=timezone.now() + timedelta(days=INVITATION_TTL_DAYS),
    )
    audit(
        actions.INVITATION_CREATED,
        organization=organization,
        actor=invited_by,
        resource=invitation,
        metadata={"email": email, "role": role_key},
    )
    return invitation, raw_token


def revoke_invitation(*, invitation: Invitation, actor: Any) -> Invitation:
    if invitation.status != Invitation.Status.PENDING:
        raise ConflictError("Only a pending invitation can be revoked.")
    invitation.status = Invitation.Status.REVOKED
    invitation.save(update_fields=["status", "updated_at"])
    audit(
        actions.INVITATION_REVOKED,
        organization=invitation.organization,
        actor=actor,
        resource=invitation,
    )
    return invitation


@transaction.atomic
def accept_invitation(
    *,
    invitation: Invitation,
    raw_token: str,
    user: Any | None,
    password: str | None = None,
    full_name: str = "",
) -> tuple[Membership, Any]:
    """Accept an invitation either as an already-authenticated ``user`` (whose
    email must match) or, when ``user`` is None, by creating a brand-new
    account for the invited email with ``password``. Never discloses *why* a
    token was rejected — expired, revoked, wrong token and email mismatch all
    surface identically."""
    from apps.businesses.models import Business
    from apps.businesses.services import grant_business_access
    from apps.identity.models import User

    if invitation.status != Invitation.Status.PENDING or invitation.expires_at <= timezone.now():
        raise InvitationInvalidError()
    if not hmac.compare_digest(invitation.token_hash, _hash_token(raw_token)):
        raise InvitationInvalidError()

    if user is not None:
        if user.email.lower() != invitation.email.lower():
            raise InvitationInvalidError()
    else:
        if not password:
            raise InvitationInvalidError("A password is required to accept this invitation.")
        if User.objects.filter(email__iexact=invitation.email).exists():
            raise ConflictError("An account already exists for this email. Log in and try again.")
        user = User.objects.create_user(
            email=invitation.email, password=password, full_name=full_name
        )

    membership, created = Membership.objects.get_or_create(
        organization=invitation.organization,
        user=user,
        defaults={
            "role": invitation.role,
            "status": Membership.Status.ACTIVE,
            "business_scope": invitation.business_scope,
            "invited_by": invitation.invited_by,
            "joined_at": timezone.now(),
        },
    )
    if not created:
        raise ConflictError("You are already a member of this organization.")

    if invitation.business_scope == Membership.BusinessScope.SELECTED and invitation.business_ids:
        businesses = Business.objects.filter(
            pk__in=invitation.business_ids, organization=invitation.organization
        )
        for business in businesses:
            grant_business_access(membership, business, actor=invitation.invited_by)

    invitation.status = Invitation.Status.ACCEPTED
    invitation.accepted_by = user
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_by", "accepted_at", "updated_at"])

    audit(
        actions.INVITATION_ACCEPTED,
        organization=invitation.organization,
        actor=user,
        resource=membership,
        metadata={"invitation_id": str(invitation.pk)},
    )
    return membership, user


def unique_organization_slug(name: str) -> str:
    """A slugified name, disambiguated with a short random suffix on
    collision — used by self-serve registration where two customers may
    plausibly pick the same organization name."""
    base = slugify(name)[:70] or "org"
    slug = base
    while Organization.objects.filter(slug=slug).exists():
        slug = f"{base}-{secrets.token_hex(3)}"
    return slug
