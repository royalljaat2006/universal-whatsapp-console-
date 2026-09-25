from typing import Any

from django.db.models import QuerySet
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.organizations.api.serializers import (
    MemberSerializer,
    MyMembershipSerializer,
    OrganizationSummarySerializer,
)
from apps.organizations.models import Membership, Organization


class MyOrganizationsView(generics.ListAPIView):
    """Organizations the caller can act in. Not tenant-scoped by design: it is
    how the client discovers which X-Organization-ID values are valid."""

    permission_classes = [IsAuthenticated]
    serializer_class = MyMembershipSerializer
    pagination_class = None

    def get_queryset(self) -> QuerySet[Membership]:
        return (
            Membership.objects.filter(
                user_id=self.request.user.pk,  # type: ignore[misc]  # IsAuthenticated guarantees a User
                status=Membership.Status.ACTIVE,
                organization__status=Organization.Status.ACTIVE,
            )
            .select_related("organization", "role")
            .prefetch_related("role__permissions")
            .order_by("organization__name")
        )


class CurrentOrganizationView(OrganizationScopedMixin, APIView):
    required_permissions = {"get": "organization.view"}

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(OrganizationSummarySerializer(self.organization).data)


class MemberListView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Membership
    serializer_class = MemberSerializer
    required_permissions = {"get": "members.view"}

    def scope_queryset(self, queryset: QuerySet[Membership]) -> QuerySet[Membership]:
        return queryset.select_related("user", "role")
