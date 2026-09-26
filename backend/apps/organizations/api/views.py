from typing import Any

from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.businesses.models import Business
from apps.contacts.models import Contact
from apps.messaging.models import Message
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.organizations.api.serializers import (
    MemberSerializer,
    MyMembershipSerializer,
    OrganizationSummarySerializer,
)
from apps.organizations.models import Membership, Organization
from apps.whatsapp.models import WhatsAppBusinessAccount, WhatsAppPhoneNumber


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


class UsageSummaryView(OrganizationScopedMixin, APIView):
    """Real, currently-countable usage — no invented quotas or plan limits.
    Foundation for future plan-based quota enforcement (see
    docs/productization/usage-metering.md): every number here already exists
    as a live count today; nothing here is metered or billed against a plan,
    because there is no Plan/Subscription model yet (deliberately out of
    scope — payment billing was not requested)."""

    required_permissions = {"get": "billing.view"}

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        organization = self.organization
        period_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return Response(
            {
                "members": Membership.objects.filter(
                    organization=organization, status=Membership.Status.ACTIVE
                ).count(),
                "businesses": Business.objects.for_organization(organization).count(),
                "whatsapp_business_accounts": WhatsAppBusinessAccount.objects.for_organization(
                    organization
                ).count(),
                "phone_numbers": WhatsAppPhoneNumber.objects.for_organization(organization).count(),
                "contacts": Contact.objects.for_organization(organization).count(),
                "messages_this_period": Message.objects.filter(
                    organization=organization, occurred_at__gte=period_start
                ).count(),
                "period_start": period_start,
            }
        )
