"""Platform (super-admin) console — cross-tenant, staff-only.

GET   /api/v1/platform/organizations/           directory (member/business counts)
POST  /api/v1/platform/organizations/           provision a new org + invite its first user
GET   /api/v1/platform/organizations/<id>/      detail
PATCH /api/v1/platform/organizations/<id>/      {"status": "active"|"suspended"}
GET   /api/v1/platform/audit-logs/              cross-organization audit trail
GET   /api/v1/platform/operations/              webhook health, DB/cache/Celery checks
GET   /api/v1/platform/webhook-events/          raw webhook deliveries (no payload — see below)
GET   /api/v1/platform/webhook-events/<id>/     detail (still no payload)
POST  /api/v1/platform/webhook-events/<id>/replay/   re-run through the processor
GET   /api/v1/platform/failed-jobs/             recent failed campaigns/imports/exports, cross-tenant

Never exposes message content or per-conversation data — only the aggregate,
operational facts a platform operator needs (org directory, audit trail,
infra health). WebhookEvent.payload in particular can contain customer
message text, so it is never serialized here (Django admin, gated
separately by is_staff + model perms, is the one place that can see it, for
genuine incident response). Every state-changing action here is itself
audit-logged.
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
from apps.campaigns.models import Campaign
from apps.contacts.models import ContactImportJob
from apps.organizations.models import Membership, Organization
from apps.organizations.permissions import DEFAULT_ROLES
from apps.organizations.services import provision_organization
from apps.platform.permissions import IsPlatformAdmin
from apps.reports.models import ExportJob
from apps.webhooks.models import WebhookEvent
from apps.webhooks.processor import mark_for_replay
from apps.webhooks.tasks import process_webhook_event
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


class ProvisionOrganizationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    owner_email = serializers.EmailField()
    # Must be one of the 5 roles seeded on every new organization — a truly
    # custom role can't exist yet for an org that doesn't exist yet. The
    # invited user can create custom roles themselves once they accept.
    role_key = serializers.ChoiceField(choices=list(DEFAULT_ROLES), default="owner")


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

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = ProvisionOrganizationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        organization, invitation, raw_token = provision_organization(
            name=payload.validated_data["name"],
            invited_email=payload.validated_data["owner_email"],
            role_key=payload.validated_data["role_key"],
            invited_by=request.user,
        )
        organization.member_count = 0  # type: ignore[attr-defined]
        organization.business_count = 0  # type: ignore[attr-defined]
        body = OrganizationDirectorySerializer(organization).data
        body["invitation"] = {
            "id": str(invitation.pk),
            "email": invitation.email,
            "role": invitation.role.key,
            "token": raw_token,
            "accept_path": f"/invitations/{invitation.pk}/accept?token={raw_token}",
        }
        return Response(body, status=status.HTTP_201_CREATED)


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


class PlatformWebhookEventSerializer(serializers.ModelSerializer):
    """Deliberately excludes ``payload`` — it can contain customer message
    text. Everything here is delivery/processing metadata only."""

    class Meta:
        model = WebhookEvent
        fields = [
            "id",
            "provider",
            "organization_ids",
            "object_type",
            "fields",
            "status",
            "attempts",
            "received_at",
            "processed_at",
            "next_attempt_at",
            "last_error",
            "request_id",
        ]
        read_only_fields = fields


class PlatformWebhookEventListView(generics.ListAPIView):
    permission_classes = [IsPlatformAdmin]
    serializer_class = PlatformWebhookEventSerializer

    def get_queryset(self) -> QuerySet[Any]:
        queryset = WebhookEvent.objects.order_by("-created_at")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        organization_id = self.request.query_params.get("organization_id")
        if organization_id:
            queryset = queryset.filter(organization_ids__contains=[organization_id])
        return queryset


class PlatformWebhookEventDetailView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        event = WebhookEvent.objects.filter(pk=pk).first()
        if event is None:
            raise NotFoundError("Webhook event not found.")
        return Response(PlatformWebhookEventSerializer(event).data)


class PlatformWebhookReplayView(APIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        event = WebhookEvent.objects.filter(pk=pk).first()
        if event is None:
            raise NotFoundError("Webhook event not found.")
        mark_for_replay(event)
        process_webhook_event.delay(str(event.pk), trigger="replay")
        audit(actions.WEBHOOK_REPLAYED, actor=request.user, resource=event, request=request)
        event.refresh_from_db()
        return Response(PlatformWebhookEventSerializer(event).data)


class PlatformFailedJobsView(APIView):
    """Cross-tenant view of recently failed background jobs — the ones an
    operator would otherwise have to find by querying each app's table
    directly. Not paginated (capped per kind): this is a triage list, not a
    full history — apps.audit / each app's own tenant-scoped endpoint has
    the complete record."""

    permission_classes = [IsPlatformAdmin]
    LIMIT = 25

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        campaigns = Campaign.objects.filter(status=Campaign.Status.FAILED).order_by("-updated_at")[
            : self.LIMIT
        ]
        imports = ContactImportJob.objects.filter(
            status=ContactImportJob.Status.FAILED
        ).order_by("-updated_at")[: self.LIMIT]
        exports = ExportJob.objects.filter(status=ExportJob.Status.FAILED).order_by("-updated_at")[
            : self.LIMIT
        ]
        return Response(
            {
                "campaigns": [
                    {
                        "id": str(c.pk),
                        "organization_id": str(c.organization_id),
                        "business_id": str(c.business_id),
                        "name": c.name,
                        "failure_code": c.failure_code,
                        "failure_reason": c.failure_reason,
                        "updated_at": c.updated_at,
                    }
                    for c in campaigns
                ],
                "contact_imports": [
                    {
                        "id": str(j.pk),
                        "organization_id": str(j.organization_id),
                        "business_id": str(j.business_id),
                        "filename": j.filename,
                        "error": j.error,
                        "updated_at": j.updated_at,
                    }
                    for j in imports
                ],
                "exports": [
                    {
                        "id": str(e.pk),
                        "organization_id": str(e.organization_id),
                        "business_id": str(e.business_id),
                        "kind": e.kind,
                        "error": e.error,
                        "updated_at": e.updated_at,
                    }
                    for e in exports
                ],
            }
        )
