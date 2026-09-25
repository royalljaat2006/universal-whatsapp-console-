"""Platform (super-admin) console — cross-tenant, staff-only.

GET   /api/v1/platform/organizations/           directory (member/business counts)
GET   /api/v1/platform/organizations/<id>/      detail
PATCH /api/v1/platform/organizations/<id>/      {"status": "active"|"suspended"}
GET   /api/v1/platform/audit-logs/              cross-organization audit trail
GET   /api/v1/platform/operations/              webhook health, DB/cache/Celery checks

Never exposes message content or per-conversation data — only the aggregate,
operational facts a platform operator needs (org directory, audit trail,
infra health). Every state-changing action here is itself audit-logged.
"""

from datetime import timedelta
from typing import Any

from django.db.models import Count, QuerySet
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import actions
from apps.audit.api import AuditLogSerializer
from apps.audit.models import AuditLog
from apps.audit.services import record as audit
from apps.businesses.models import Business
from apps.organizations.models import Membership, Organization
from apps.platform.permissions import IsPlatformAdmin
from apps.webhooks.models import WebhookEvent
from core.exceptions import NotFoundError
from core.health.checks import check_cache, check_celery_workers, check_database


class OrganizationDirectorySerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)
    business_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "slug",
            "status",
            "timezone",
            "member_count",
            "business_count",
            "created_at",
        ]
        read_only_fields = fields


class OrganizationDirectoryView(generics.ListAPIView):
    """TenantScopedModel FKs use related_name="+" (no reverse accessor, by
    design — see apps.organizations.tenancy), so counts are computed as
    separate grouped queries and merged in Python rather than annotated."""

    permission_classes = [IsPlatformAdmin]
    serializer_class = OrganizationDirectorySerializer
    pagination_class = None
    # Returns a plain list (counts merged in Python, see class docstring),
    # not a QuerySet — DRF's default OrderingFilter would call .order_by() on
    # it if an ordering param were ever accepted, so it's disabled here.
    filter_backends: list[Any] = []

    def get_queryset(self) -> QuerySet[Any]:
        organizations = list(Organization.objects.order_by("name"))
        member_counts = dict(
            Membership.objects.values_list("organization_id")
            .annotate(count=Count("id"))
            .values_list("organization_id", "count")
        )
        business_counts = dict(
            Business.objects.values_list("organization_id")
            .annotate(count=Count("id"))
            .values_list("organization_id", "count")
        )
        for organization in organizations:
            organization.member_count = member_counts.get(organization.pk, 0)  # type: ignore[attr-defined]
            organization.business_count = business_counts.get(  # type: ignore[attr-defined]
                organization.pk, 0
            )
        return organizations  # type: ignore[return-value]


class UpdateOrganizationStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Organization.Status.choices)


class OrganizationAdminDetailView(APIView):
    permission_classes = [IsPlatformAdmin]

    def _organization(self, pk: str) -> Organization:
        organization = Organization.objects.filter(pk=pk).first()
        if organization is None:
            raise NotFoundError("Organization not found.")
        return organization

    def get(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        organization = self._organization(pk)
        body = {
            "id": str(organization.pk),
            "name": organization.name,
            "slug": organization.slug,
            "status": organization.status,
            "timezone": organization.timezone,
            "created_at": organization.created_at,
            "member_count": Membership.objects.filter(organization=organization).count(),
            "business_count": Business.objects.filter(organization=organization).count(),
        }
        return Response(body)

    def patch(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        organization = self._organization(pk)
        payload = UpdateOrganizationStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        new_status = payload.validated_data["status"]
        if new_status == organization.status:
            return Response(OrganizationDirectorySerializer(organization).data)
        organization.status = new_status
        organization.save(update_fields=["status", "updated_at"])
        action = (
            actions.ORGANIZATION_SUSPENDED
            if new_status == Organization.Status.SUSPENDED
            else actions.ORGANIZATION_REACTIVATED
        )
        audit(
            action,
            organization=organization,
            actor=request.user,
            resource=organization,
            request=request,
        )
        return Response(OrganizationDirectorySerializer(organization).data)


class PlatformAuditLogSerializer(AuditLogSerializer):
    """Adds the organization id: unlike the tenant-scoped audit endpoint
    (apps.audit.api), a cross-organization listing needs it to be legible."""

    organization: Any = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta(AuditLogSerializer.Meta):
        fields = [*AuditLogSerializer.Meta.fields, "organization"]
        read_only_fields = fields


class PlatformAuditLogListView(generics.ListAPIView):
    permission_classes = [IsPlatformAdmin]
    serializer_class = PlatformAuditLogSerializer

    def get_queryset(self) -> QuerySet[Any]:
        queryset = AuditLog.objects.select_related("actor", "organization").order_by("-created_at")
        organization_id = self.request.query_params.get("organization_id")
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        action = self.request.query_params.get("action")
        if action:
            queryset = queryset.filter(action=action)
        return queryset


class OperationsSummaryView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        since = timezone.now() - timedelta(hours=24)
        webhook_counts = {
            row["status"]: row["count"]
            for row in WebhookEvent.objects.filter(created_at__gte=since)
            .values("status")
            .annotate(count=Count("id"))
        }
        checks = [check_database(), check_cache(), check_celery_workers()]
        return Response(
            {
                "organizations": {
                    "total": Organization.objects.count(),
                    "active": Organization.objects.filter(
                        status=Organization.Status.ACTIVE
                    ).count(),
                    "suspended": Organization.objects.filter(
                        status=Organization.Status.SUSPENDED
                    ).count(),
                },
                "webhooks_last_24h": {
                    "dead": webhook_counts.get(WebhookEvent.Status.DEAD, 0),
                    "failed": webhook_counts.get(WebhookEvent.Status.FAILED, 0),
                    "processed": webhook_counts.get(WebhookEvent.Status.PROCESSED, 0),
                    "partial": webhook_counts.get(WebhookEvent.Status.PARTIAL, 0),
                },
                "infrastructure": {c.name: c.as_dict() for c in checks},
            },
            status=status.HTTP_200_OK,
        )
