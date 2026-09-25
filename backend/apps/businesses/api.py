"""Businesses (workspaces) API.

GET  /api/v1/businesses/         businesses the caller can access
POST /api/v1/businesses/         create (businesses.manage)
GET  /api/v1/businesses/<id>/    detail
PATCH /api/v1/businesses/<id>/   update name/description/status/timezone/metadata
"""

from typing import Any

from django.db.models import Count, QuerySet
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit import actions
from apps.audit.services import record as audit
from apps.businesses.models import Business
from apps.businesses.services import create_business
from apps.organizations.api.scoping import OrganizationScopedMixin


class BusinessSerializer(serializers.ModelSerializer):
    business_account_count = serializers.IntegerField(read_only=True)
    phone_number_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Business
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "status",
            "timezone",
            "metadata",
            "business_account_count",
            "phone_number_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "slug",
            "business_account_count",
            "phone_number_count",
            "created_at",
            "updated_at",
        ]


class BusinessCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    slug = serializers.SlugField(max_length=80, required=False)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)


def _annotated(queryset: QuerySet[Any]) -> QuerySet[Any]:
    return queryset.annotate(
        business_account_count=Count("business_accounts", distinct=True),
        phone_number_count=Count("phone_numbers", distinct=True),
    )


class BusinessListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Business
    business_field = "pk"
    serializer_class = BusinessSerializer
    pagination_class = None  # bounded per organization; used for the selector
    required_permissions = {"get": "organization.view", "post": "businesses.manage"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return _annotated(queryset.exclude(status=Business.Status.ARCHIVED)).order_by("name")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = BusinessCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        business = create_business(
            organization=self.organization,
            actor=request.user,
            request=request,
            **payload.validated_data,
        )
        business = _annotated(Business.objects.filter(pk=business.pk)).get()
        return Response(BusinessSerializer(business).data, status=status.HTTP_201_CREATED)


class BusinessDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    queryset_model = Business
    business_field = "pk"
    serializer_class = BusinessSerializer
    required_permissions = {
        "get": "organization.view",
        "patch": "businesses.manage",
        "put": "businesses.manage",
    }
    http_method_names = ["get", "patch", "head", "options"]

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return _annotated(queryset)

    def perform_update(self, serializer: serializers.BaseSerializer) -> None:
        business = serializer.save()
        audit(
            actions.BUSINESS_UPDATED,
            organization=self.organization,
            actor=self.request.user,
            resource=business,
            metadata={"fields": sorted(serializer.validated_data)},
            request=self.request,
        )
