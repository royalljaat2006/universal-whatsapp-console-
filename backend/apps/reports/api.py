"""Export jobs.

POST /api/v1/exports/                {"kind", "params"}  -> 202 (job queued)
GET  /api/v1/exports/                the caller's business exports
GET  /api/v1/exports/<id>/           status
GET  /api/v1/exports/<id>/download/  the CSV (streamed)

Requires ``reports.export`` **and** the view permission of the exported data
(campaigns.view / conversations.view / billing.view / analytics.view). The
business is the validated X-Business-ID; ids in ``params`` must belong to it.
"""

from datetime import date
from typing import Any

from django.db import transaction
from django.http import StreamingHttpResponse
from rest_framework import generics, serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import actions
from apps.audit.services import record as audit
from apps.campaigns.models import Campaign
from apps.contacts.api import BusinessRequiredError
from apps.contacts.models import Tag
from apps.media import storage
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.organizations.services import has_permission
from apps.reports.models import ExportJob
from apps.whatsapp.models import WhatsAppPhoneNumber
from core.api.pagination import StandardCursorPagination
from core.exceptions import DomainError, NotFoundError
from core.limits import enforce_cap

MAX_EXPORT_DAYS = 400
DATA_PERMISSION = {
    ExportJob.Kind.CAMPAIGN_RECIPIENTS: "campaigns.view",
    ExportJob.Kind.CAMPAIGN_SUMMARY: "campaigns.view",
    ExportJob.Kind.MESSAGES: "conversations.view",
    ExportJob.Kind.COSTS: "billing.view",
    ExportJob.Kind.ANALYTICS_DAILY: "analytics.view",
    ExportJob.Kind.CONTACTS: "contacts.view",
}


class ExportSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    requested_by_email = serializers.EmailField(
        source="requested_by.email", read_only=True, default=None
    )
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ExportJob
        fields = [
            "id",
            "business_id",
            "kind",
            "params",
            "status",
            "row_count",
            "file_size",
            "filename",
            "error",
            "started_at",
            "finished_at",
            "requested_by_email",
            "created_at",
            "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj: ExportJob) -> str | None:
        if obj.status != ExportJob.Status.SUCCEEDED:
            return None
        return f"/api/v1/exports/{obj.pk}/download/"


def _is_uuid(raw: Any) -> bool:
    import uuid

    try:
        uuid.UUID(str(raw))
    except ValueError:
        return False
    return True


def _dates(params: dict[str, Any]) -> dict[str, str]:
    try:
        date_from = date.fromisoformat(str(params.get("date_from")))
        date_to = date.fromisoformat(str(params.get("date_to")))
    except ValueError as exc:
        raise DomainError("date_from and date_to (YYYY-MM-DD) are required.") from exc
    if date_from > date_to or (date_to - date_from).days + 1 > MAX_EXPORT_DAYS:
        raise DomainError(f"Invalid range (at most {MAX_EXPORT_DAYS} days).")
    return {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}


class ExportPagination(StandardCursorPagination):
    ordering = ("-created_at", "-id")


class _ExportMixin(OrganizationScopedMixin):
    def export(self, pk: Any) -> ExportJob:
        job = (
            self.restrict_to_businesses(
                ExportJob.objects.for_organization(self.organization), "business"
            )
            .select_related("requested_by")
            .filter(pk=pk)
            .first()
        )
        if job is None:
            raise NotFoundError("export not found")
        return job


class ExportListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = ExportJob
    business_field = "business"
    serializer_class = ExportSerializer
    pagination_class = ExportPagination
    required_permissions = {"get": "reports.export", "post": "reports.export"}

    def scope_queryset(self, queryset: Any) -> Any:
        return queryset.select_related("requested_by")

    def _params(self, kind: str, raw: dict[str, Any]) -> tuple[dict[str, Any], str]:
        business = self.business
        if kind in {ExportJob.Kind.CAMPAIGN_RECIPIENTS, ExportJob.Kind.CAMPAIGN_SUMMARY}:
            campaign_id = str(raw.get("campaign_id") or "")
            campaign = (
                Campaign.objects.filter(pk=campaign_id, business=business).first()
                if _is_uuid(campaign_id)
                else None
            )
            if campaign is None:
                raise NotFoundError("campaign not found")
            slug = "recipients" if kind == ExportJob.Kind.CAMPAIGN_RECIPIENTS else "summary"
            return {"campaign_id": str(campaign.pk)}, f"campaign-{slug}-{campaign.pk}.csv"
        if kind == ExportJob.Kind.CONTACTS:
            params: dict[str, Any] = {}
            if raw.get("tag_id"):
                if (
                    not _is_uuid(raw["tag_id"])
                    or not Tag.objects.filter(pk=raw["tag_id"], business=business).exists()
                ):
                    raise NotFoundError("tag not found")
                params["tag_id"] = str(raw["tag_id"])
            return params, "contacts.csv"
        params = _dates(raw)
        if kind == ExportJob.Kind.MESSAGES:
            if raw.get("phone_number_id"):
                if (
                    not _is_uuid(raw["phone_number_id"])
                    or not WhatsAppPhoneNumber.objects.filter(
                        pk=raw["phone_number_id"], business=business
                    ).exists()
                ):
                    raise NotFoundError("phone number not found")
                params["phone_number_id"] = str(raw["phone_number_id"])
            if raw.get("direction") in {"inbound", "outbound"}:
                params["direction"] = raw["direction"]
            if raw.get("campaign_id"):
                if (
                    not _is_uuid(raw["campaign_id"])
                    or not Campaign.objects.filter(
                        pk=raw["campaign_id"], business=business
                    ).exists()
                ):
                    raise NotFoundError("campaign not found")
                params["campaign_id"] = str(raw["campaign_id"])
        return params, f"{kind}-{params['date_from']}-{params['date_to']}.csv"

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from apps.reports.tasks import run_export

        if self.business is None:
            raise BusinessRequiredError()
        kind = request.data.get("kind")
        if kind not in ExportJob.Kind.values:
            raise DomainError("Unknown export kind.", details={"allowed": ExportJob.Kind.values})
        needed = DATA_PERMISSION[kind]
        if not has_permission(self.membership, needed):
            raise PermissionDenied(f"'{needed}' is required for this export.")
        raw = request.data.get("params") or {}
        if not isinstance(raw, dict):
            raise DomainError("params must be an object.")
        params, filename = self._params(kind, raw)
        enforce_cap(
            setting="MAX_ACTIVE_EXPORTS_PER_ORG",
            current=ExportJob.objects.for_organization(self.organization)
            .filter(status__in=[ExportJob.Status.PENDING, ExportJob.Status.RUNNING])
            .count(),
            noun="exports in progress",
            hint="Wait for them to finish, then try again.",
        )
        with transaction.atomic():
            job = ExportJob.objects.create(
                organization=self.organization,
                business=self.business,
                kind=kind,
                params=params,
                filename=filename,
                requested_by=request.user if request.user.is_authenticated else None,
            )
            audit(
                actions.EXPORT_REQUESTED,
                organization=self.organization,
                actor=request.user,
                resource=job,
                metadata={"business_id": str(self.business.pk), "kind": kind, "params": params},
                request=request,
            )
            job_id = str(job.pk)
            transaction.on_commit(lambda: run_export.delay(job_id))
        return Response(ExportSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class ExportDetailView(_ExportMixin, APIView):
    required_permissions = {"get": "reports.export"}

    def get(self, request: Request, pk: str) -> Response:
        return Response(ExportSerializer(self.export(pk)).data)


class ExportDownloadView(_ExportMixin, APIView):
    required_permissions = {"get": "reports.export"}

    def get(self, request: Request, pk: str) -> StreamingHttpResponse:
        job = self.export(pk)
        if job.status != ExportJob.Status.SUCCEEDED or not job.storage_key:
            raise DomainError("The export is not ready.", details={"status": job.status})
        audit(
            actions.EXPORT_DOWNLOADED,
            organization=self.organization,
            actor=request.user,
            resource=job,
            metadata={"business_id": str(job.business_id), "kind": job.kind},
            request=request,
        )
        response = StreamingHttpResponse(
            storage.iter_chunks(job.storage_key), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{job.filename.replace(chr(34), "")}"'
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        if job.file_size is not None:
            response["Content-Length"] = str(job.file_size)
        return response
