"""Tenant- and business-scoped API building blocks. Every tenant endpoint
inherits ``OrganizationScopedMixin``; it is the server-side isolation boundary.

Organization: ``X-Organization-ID`` header, honoured only for an ACTIVE
membership in an ACTIVE organization. Unknown or foreign ids -> 404 (not 403)
so tenant existence is not disclosed.

Business (workspace): optional ``X-Business-ID`` header. When present it must
be a business of that organization the membership may access (else 404) and
narrows every business-owned queryset to it. When absent, querysets are still
restricted to the businesses the membership may access. The client never
decides visibility.
"""

import uuid
from typing import Any, ClassVar

from django.db.models import Model, QuerySet
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.businesses.models import Business
from apps.businesses.services import accessible_business_ids, get_business_for_membership
from apps.organizations.models import Membership, Organization
from apps.organizations.services import get_active_membership, has_permission
from core.context import organization_id_var
from core.exceptions import OrganizationRequiredError

ORGANIZATION_HEADER = "X-Organization-ID"
BUSINESS_HEADER = "X-Business-ID"


def _uuid_or_404(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFound(f"{what} not found.") from exc


def resolve_membership(request: Request) -> Membership:
    raw = request.headers.get(ORGANIZATION_HEADER, "").strip()
    if not raw:
        raise OrganizationRequiredError()
    membership = get_active_membership(request.user, _uuid_or_404(raw, "Organization"))
    if membership is None:
        raise NotFound("Organization not found.")
    return membership


def resolve_business(request: Request, membership: Membership) -> Business | None:
    raw = request.headers.get(BUSINESS_HEADER, "").strip()
    if not raw:
        return None
    return get_business_for_membership(membership, _uuid_or_404(raw, "Business"))


class HasOrganizationPermission(BasePermission):
    """Checks ``view.required_permissions[action]`` (or ``["*"]``) against the
    membership's role. Views without a declared permission are denied —
    forgetting to declare one fails closed."""

    def has_permission(self, request: Request, view: Any) -> bool:
        membership: Membership | None = getattr(request, "membership", None)
        required = getattr(view, "required_permissions", None)
        if membership is None or not required:
            return False
        action = getattr(view, "action", None) or (request.method or "get").lower()
        code = required.get(action, required.get("*"))
        if code is None:
            raise PermissionDenied("This action is not permitted.")
        if not has_permission(membership, code):
            raise PermissionDenied(f"Missing permission: {code}")
        return True


class OrganizationScopedMixin(APIView):
    """Resolves tenant/business context before permission checks and scopes
    querysets.

    Subclasses set ``queryset_model``, ``required_permissions`` and, for
    business-owned data, ``business_field`` (the lookup path to the owning
    business, e.g. ``"business"``). They override ``scope_queryset`` for
    select_related/filters, never ``get_queryset`` directly.
    """

    permission_classes = [IsAuthenticated, HasOrganizationPermission]
    queryset_model: ClassVar[type[Model]]
    required_permissions: ClassVar[dict[str, str]] = {}
    business_field: ClassVar[str | None] = None

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        # Authentication runs first (inside super().initial) — we resolve the
        # membership between authentication and permission checks.
        self.perform_authentication(request)
        if request.user and request.user.is_authenticated:
            membership = resolve_membership(request)
            request.membership = membership  # type: ignore[attr-defined]
            request.organization = membership.organization  # type: ignore[attr-defined]
            organization_id_var.set(str(membership.organization_id))
            request.business = resolve_business(request, membership)  # type: ignore[attr-defined]
        super().initial(request, *args, **kwargs)

    @property
    def organization(self) -> Organization:
        organization: Organization = self.request.organization  # type: ignore[attr-defined]
        return organization

    @property
    def membership(self) -> Membership:
        membership: Membership = self.request.membership  # type: ignore[attr-defined]
        return membership

    @property
    def business(self) -> Business | None:
        business: Business | None = self.request.business  # type: ignore[attr-defined]
        return business

    @property
    def allowed_business_ids(self) -> set[Any] | None:
        return accessible_business_ids(self.membership)

    def restrict_to_businesses(self, queryset: QuerySet[Any], field: str) -> QuerySet[Any]:
        """Apply business visibility to any queryset (usable outside get_queryset)."""
        if self.business is not None:
            return queryset.filter(**{field: self.business.pk})
        allowed = self.allowed_business_ids
        if allowed is not None:
            return queryset.filter(**{f"{field}__in": allowed})
        return queryset

    def get_queryset(self) -> QuerySet[Any]:
        base = self.queryset_model.objects.for_organization(self.organization)  # type: ignore[attr-defined]
        if self.business_field:
            base = self.restrict_to_businesses(base, self.business_field)
        return self.scope_queryset(base)

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return queryset
