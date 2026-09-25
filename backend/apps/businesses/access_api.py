"""Business access management (owners/admins; ``members.manage``).

GET    /api/v1/businesses/<id>/access/                 members and their access to this business
POST   /api/v1/businesses/<id>/access/                 {"membership_id"} grant
DELETE /api/v1/businesses/<id>/access/<membership_id>/ revoke
PATCH  /api/v1/organizations/current/members/<id>/     {"business_scope": "all"|"selected"}

Rules (server-enforced): nobody changes their own access; owners always see
every business; the business must belong to the caller's organization.
"""

from typing import Any

from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.businesses.models import Business, BusinessAccess
from apps.businesses.services import (
    get_business_for_membership,
    grant_access_as,
    revoke_business_access,
    set_business_scope,
)
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.organizations.models import Membership
from core.exceptions import NotFoundError


def _member(organization_id: Any, membership_id: Any) -> Membership:
    membership = (
        Membership.objects.select_related("user", "role", "organization")
        .filter(pk=membership_id, organization_id=organization_id)
        .first()
    )
    if membership is None:
        raise NotFoundError("Member not found.")
    return membership


class BusinessAccessView(OrganizationScopedMixin, APIView):
    required_permissions = {"get": "members.manage", "post": "members.manage"}

    def _business(self, pk: Any) -> Business:
        return get_business_for_membership(self.membership, pk)

    def get(self, request: Request, pk: str) -> Response:
        business = self._business(pk)
        granted = set(
            BusinessAccess.objects.filter(business=business).values_list("membership_id", flat=True)
        )
        rows = []
        for m in (
            Membership.objects.filter(organization=self.organization)
            .select_related("user", "role")
            .order_by("user__email")
        ):
            via_scope = m.business_scope == Membership.BusinessScope.ALL
            rows.append(
                {
                    "membership_id": str(m.pk),
                    "email": m.user.email,
                    "name": m.user.full_name,
                    "role": m.role.key,
                    "status": m.status,
                    "business_scope": m.business_scope,
                    "has_access": via_scope or m.pk in granted,
                    "access_via": (
                        "all_businesses" if via_scope else ("grant" if m.pk in granted else None)
                    ),
                    "is_self": m.pk == self.membership.pk,
                    "manageable": m.pk != self.membership.pk and m.role.key != "owner",
                }
            )
        return Response(
            {"business": {"id": str(business.pk), "name": business.name}, "members": rows}
        )

    def post(self, request: Request, pk: str) -> Response:
        business = self._business(pk)
        target = _member(self.organization.pk, request.data.get("membership_id"))
        grant_access_as(target, business, actor_membership=self.membership)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BusinessAccessRevokeView(OrganizationScopedMixin, APIView):
    required_permissions = {"delete": "members.manage"}

    def delete(self, request: Request, pk: str, membership_id: str) -> Response:
        business = get_business_for_membership(self.membership, pk)
        target = _member(self.organization.pk, membership_id)
        revoke_business_access(target, business, actor_membership=self.membership)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ScopeSerializer(serializers.Serializer):
    business_scope = serializers.ChoiceField(choices=Membership.BusinessScope.choices)


class MemberScopeView(OrganizationScopedMixin, APIView):
    required_permissions = {"patch": "members.manage"}

    def patch(self, request: Request, membership_id: str) -> Response:
        payload = ScopeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        target = _member(self.organization.pk, membership_id)
        set_business_scope(
            target, payload.validated_data["business_scope"], actor_membership=self.membership
        )
        return Response({"membership_id": str(target.pk), "business_scope": target.business_scope})
