"""Business management and business-level access control (public interface)."""

from typing import Any

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from apps.audit import actions
from apps.audit.services import record as audit
from apps.businesses.models import Business, BusinessAccess
from apps.organizations.models import Membership, Organization
from core.exceptions import ConflictError, DomainError, NotFoundError


def accessible_business_ids(membership: Membership) -> set[Any] | None:
    """``None`` means every business of the organization; otherwise the set of
    granted business ids (possibly empty)."""
    if membership.business_scope == Membership.BusinessScope.ALL:
        return None
    cached = getattr(membership, "_business_ids", None)
    if cached is None:
        cached = set(
            BusinessAccess.objects.filter(membership=membership).values_list(
                "business_id", flat=True
            )
        )
        membership._business_ids = cached  # type: ignore[attr-defined]
    return cached


def can_access_business(membership: Membership, business: Business | Any) -> bool:
    business_id = getattr(business, "pk", business)
    allowed = accessible_business_ids(membership)
    return allowed is None or business_id in allowed


def get_business_for_membership(membership: Membership, business_id: Any) -> Business:
    """Resolve a client-supplied business id: it must belong to the membership's
    organization, be active, and be accessible. Otherwise 404 (no oracle)."""
    business = (
        Business.objects.for_organization(membership.organization_id)
        .filter(pk=business_id)
        .exclude(status=Business.Status.ARCHIVED)
        .first()
    )
    if business is None or not can_access_business(membership, business):
        raise NotFoundError("Business not found.")
    return business


@transaction.atomic
def create_business(
    *,
    organization: Organization,
    name: str,
    slug: str | None = None,
    description: str = "",
    actor: Any = None,
    request: Any = None,
) -> Business:
    slug = slug or slugify(name)[:80]
    try:
        with transaction.atomic():
            business = Business.objects.create(
                organization=organization, name=name, slug=slug, description=description
            )
    except IntegrityError as exc:
        raise ConflictError(f"a business with slug '{slug}' already exists") from exc
    audit(
        actions.BUSINESS_CREATED,
        organization=organization,
        actor=actor,
        resource=business,
        metadata={"name": name, "slug": slug},
        request=request,
    )
    return business


@transaction.atomic
def grant_business_access(membership: Membership, business: Business, *, actor: Any = None) -> None:
    if business.organization_id != membership.organization_id:
        raise NotFoundError("Business not found.")
    BusinessAccess.objects.get_or_create(
        organization_id=membership.organization_id, membership=membership, business=business
    )
    if membership.business_scope != Membership.BusinessScope.SELECTED:
        membership.business_scope = Membership.BusinessScope.SELECTED
        membership.save(update_fields=["business_scope", "updated_at"])
    audit(
        actions.BUSINESS_ACCESS_GRANTED,
        organization=membership.organization,
        actor=actor,
        resource=membership,
        metadata={"business_id": str(business.pk)},
    )


class AccessChangeForbiddenError(DomainError):
    code = "ACCESS_CHANGE_FORBIDDEN"
    status_code = 403


def _guard_target(actor_membership: Membership, target: Membership) -> None:
    if target.organization_id != actor_membership.organization_id:
        raise NotFoundError("Member not found.")
    if target.pk == actor_membership.pk:
        raise AccessChangeForbiddenError("You cannot change your own business access.")
    if target.role.key == "owner":
        raise AccessChangeForbiddenError("Owners always have access to every business.")


@transaction.atomic
def revoke_business_access(
    target: Membership, business: Business, *, actor_membership: Membership
) -> None:
    """Remove a member's access to one business. A member who could see all
    businesses is converted to an explicit list of the remaining ones."""
    _guard_target(actor_membership, target)
    if business.organization_id != target.organization_id:
        raise NotFoundError("Business not found.")
    if target.business_scope == Membership.BusinessScope.ALL:
        others = Business.objects.filter(organization_id=target.organization_id).exclude(
            pk=business.pk
        )
        for other in others:
            BusinessAccess.objects.get_or_create(
                organization_id=target.organization_id, membership=target, business=other
            )
        target.business_scope = Membership.BusinessScope.SELECTED
        target.save(update_fields=["business_scope", "updated_at"])
    BusinessAccess.objects.filter(membership=target, business=business).delete()
    audit(
        actions.BUSINESS_ACCESS_REVOKED,
        organization=target.organization,
        actor=actor_membership.user,
        resource=target,
        metadata={"business_id": str(business.pk)},
    )


@transaction.atomic
def grant_access_as(
    target: Membership, business: Business, *, actor_membership: Membership
) -> None:
    _guard_target(actor_membership, target)
    grant_business_access(target, business, actor=actor_membership.user)


@transaction.atomic
def set_business_scope(
    target: Membership, scope: str, *, actor_membership: Membership
) -> Membership:
    _guard_target(actor_membership, target)
    if scope not in Membership.BusinessScope.values:
        raise DomainError("Invalid business scope.")
    target.business_scope = scope
    target.save(update_fields=["business_scope", "updated_at"])
    audit(
        actions.BUSINESS_ACCESS_SCOPE_CHANGED,
        organization=target.organization,
        actor=actor_membership.user,
        resource=target,
        metadata={"business_scope": scope},
    )
    return target
