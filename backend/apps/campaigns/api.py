"""Campaign API.

View:   campaigns.view    list/detail/report/recipients/failures/events/preview
Edit:   campaigns.manage  create, edit (draft), delete (draft), audiences
Run:    campaigns.launch  launch (explicit confirm), pause, resume, cancel, retry-failed

Everything is business-scoped: list/detail querysets are restricted to the
caller's accessible businesses, and writes use the validated X-Business-ID
business, never a business id from the body.
"""

from typing import Any

from django.db.models import QuerySet
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.campaigns import audience as audience_mod
from apps.campaigns import checks, engine, progress, reporting, services
from apps.campaigns.models import Audience, Campaign, CampaignEvent, CampaignRecipient
from apps.contacts.api import BusinessRequiredError
from apps.organizations.api.scoping import OrganizationScopedMixin
from core.api.pagination import StandardCursorPagination
from core.exceptions import DomainError, NotFoundError


class CampaignSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    phone_number_id = serializers.UUIDField(read_only=True)
    template_id = serializers.UUIDField(read_only=True)
    audience_id = serializers.UUIDField(read_only=True, allow_null=True)
    phone_number_display = serializers.CharField(
        source="phone_number.display_phone_number", read_only=True
    )
    template_name = serializers.CharField(source="template.name", read_only=True)
    template_language = serializers.CharField(source="template.language", read_only=True)
    template_category = serializers.CharField(source="template.category", read_only=True)
    created_by_email = serializers.EmailField(
        source="created_by.email", read_only=True, default=None
    )
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = [
            "id",
            "business_id",
            "name",
            "description",
            "status",
            "phone_number_id",
            "phone_number_display",
            "template_id",
            "template_name",
            "template_language",
            "template_category",
            "audience_id",
            "audience_definition",
            "variable_mapping",
            "max_messages_per_minute",
            "scheduled_at",
            "timezone",
            "metadata",
            "audience_count",
            "snapshot_built_at",
            "launched_at",
            "started_at",
            "paused_at",
            "completed_at",
            "cancelled_at",
            "failure_code",
            "failure_reason",
            "created_by_email",
            "created_at",
            "updated_at",
            "progress",
        ]
        read_only_fields = fields

    def get_progress(self, obj: Campaign) -> dict[str, Any]:
        cached = self.context.get("progress", {})
        return cached.get(obj.pk) or progress.counts(obj.pk)


class CampaignPagination(StandardCursorPagination):
    ordering = ("-created_at", "-id")


def _campaigns(queryset: QuerySet[Campaign]) -> QuerySet[Campaign]:
    return queryset.select_related("phone_number", "template", "created_by")


class _CampaignObjectMixin(OrganizationScopedMixin):
    def campaign(self, pk: Any) -> Campaign:
        qs = self.restrict_to_businesses(
            _campaigns(Campaign.objects.for_organization(self.organization)), "business"
        ).select_related("business__organization", "phone_number__business_account")
        campaign = qs.filter(pk=pk).first()
        if campaign is None:
            raise NotFoundError("campaign not found")
        return campaign

    def respond(self, campaign: Campaign, code: int = status.HTTP_200_OK) -> Response:
        campaign = self.campaign(campaign.pk)
        return Response(CampaignSerializer(campaign).data, status=code)


class CampaignListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Campaign
    business_field = "business"
    serializer_class = CampaignSerializer
    pagination_class = CampaignPagination
    required_permissions = {"get": "campaigns.view", "post": "campaigns.manage"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        params = self.request.query_params
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        q = (params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(name__icontains=q)
        return _campaigns(queryset)

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        items = list(page or [])
        context = {
            **self.get_serializer_context(),
            "progress": progress.bulk_counts([c.pk for c in items]),
        }
        data = CampaignSerializer(items, many=True, context=context).data
        return self.get_paginated_response(data)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if self.business is None:
            raise BusinessRequiredError()
        if not isinstance(request.data, dict):
            raise DomainError("A JSON object is required.")
        campaign = services.create_campaign(
            business=self.business, data=dict(request.data), actor=request.user, request=request
        )
        return Response(
            CampaignSerializer(_campaigns(Campaign.objects.filter(pk=campaign.pk)).get()).data,
            status=status.HTTP_201_CREATED,
        )


class CampaignDetailView(_CampaignObjectMixin, APIView):
    required_permissions = {
        "get": "campaigns.view",
        "patch": "campaigns.manage",
        "delete": "campaigns.manage",
    }

    def get(self, request: Request, pk: str) -> Response:
        return Response(CampaignSerializer(self.campaign(pk)).data)

    def patch(self, request: Request, pk: str) -> Response:
        campaign = self.campaign(pk)
        if not isinstance(request.data, dict):
            raise DomainError("A JSON object is required.")
        updated = services.update_campaign(
            campaign, data=dict(request.data), actor=request.user, request=request
        )
        return self.respond(updated)

    def delete(self, request: Request, pk: str) -> Response:
        services.delete_campaign(self.campaign(pk), actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CampaignPreviewView(_CampaignObjectMixin, APIView):
    """Launch summary: every check + audience preview. Read-only."""

    required_permissions = {"get": "campaigns.view"}

    def get(self, request: Request, pk: str) -> Response:
        campaign = self.campaign(pk)
        report = checks.evaluate(campaign, audience=campaign.snapshot_built_at is None)
        data: dict[str, Any] = report.as_dict()
        if campaign.snapshot_built_at is None:
            try:
                data["audience"] = audience_mod.preview(
                    campaign.business, campaign.audience_definition
                )
            except audience_mod.AudienceDefinitionError as exc:
                data["audience"] = {"error": exc.details or exc.message}
        return Response(data)


class CampaignLaunchView(_CampaignObjectMixin, APIView):
    required_permissions = {"post": "campaigns.launch"}

    def post(self, request: Request, pk: str) -> Response:
        campaign = self.campaign(pk)
        if request.data.get("confirm") is not True:
            raise DomainError(
                'Launching requires explicit confirmation ({"confirm": true}).',
                details={"confirm": "required"},
            )
        launched, report = engine.launch(campaign.pk, actor=request.user, request=request)
        body = CampaignSerializer(self.campaign(launched.pk)).data
        return Response(
            {"campaign": body, "launch": report.as_dict()}, status=status.HTTP_202_ACCEPTED
        )


class _ControlView(_CampaignObjectMixin, APIView):
    required_permissions = {"post": "campaigns.launch"}
    action_name = ""

    def post(self, request: Request, pk: str) -> Response:
        campaign = self.campaign(pk)
        result: Any = getattr(engine, self.action_name)(
            campaign.pk, actor=request.user, request=request
        )
        body = CampaignSerializer(self.campaign(campaign.pk)).data
        extra = (
            result
            if isinstance(result, dict)
            else ({"recipients": result} if isinstance(result, int) else {})
        )
        return Response({"campaign": body, **extra})


class CampaignPauseView(_ControlView):
    action_name = "pause"


class CampaignResumeView(_ControlView):
    action_name = "resume"


class CampaignCancelView(_ControlView):
    action_name = "cancel"


class CampaignRetryView(_ControlView):
    action_name = "retry_failed"


class CampaignReportView(_CampaignObjectMixin, APIView):
    required_permissions = {"get": "campaigns.view"}

    def get(self, request: Request, pk: str) -> Response:
        return Response(reporting.report(self.campaign(pk)))


class CampaignFailuresView(_CampaignObjectMixin, APIView):
    required_permissions = {"get": "campaigns.view"}

    def get(self, request: Request, pk: str) -> Response:
        return Response(reporting.failures(self.campaign(pk)))


class RecipientSerializer(serializers.ModelSerializer):
    contact_id = serializers.UUIDField(read_only=True)
    message_id = serializers.UUIDField(read_only=True, allow_null=True)
    contact_name = serializers.SerializerMethodField()

    class Meta:
        model = CampaignRecipient
        fields = [
            "id",
            "contact_id",
            "contact_name",
            "phone_number",
            "status",
            "status_changed_at",
            "attempts",
            "skip_reason",
            "error_code",
            "error_message",
            "failure_category",
            "message_id",
            "variables",
        ]
        read_only_fields = fields

    def get_contact_name(self, obj: CampaignRecipient) -> str:
        return obj.contact.display_name or obj.contact.profile_name


class RecipientPagination(StandardCursorPagination):
    ordering = ("id",)


class CampaignRecipientsView(_CampaignObjectMixin, generics.ListAPIView):
    serializer_class = RecipientSerializer
    pagination_class = RecipientPagination
    required_permissions = {"get": "campaigns.view"}

    def get_queryset(self) -> QuerySet[Any]:
        campaign = self.campaign(self.kwargs["pk"])
        qs = CampaignRecipient.objects.filter(campaign=campaign).select_related("contact")
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("failure_category"):
            qs = qs.filter(failure_category=params["failure_category"])
        return qs


class EventSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = CampaignEvent
        fields = ["id", "event", "actor_email", "data", "created_at"]
        read_only_fields = fields


class CampaignEventsView(_CampaignObjectMixin, APIView):
    required_permissions = {"get": "campaigns.view"}

    def get(self, request: Request, pk: str) -> Response:
        campaign = self.campaign(pk)
        events = CampaignEvent.objects.filter(campaign=campaign).select_related("actor")[:500]
        return Response(EventSerializer(events, many=True).data)


class AudiencePreviewView(OrganizationScopedMixin, APIView):
    """Preview an audience definition without saving it (count + sample)."""

    required_permissions = {"post": "campaigns.view"}

    def post(self, request: Request) -> Response:
        if self.business is None:
            raise BusinessRequiredError()
        return Response(audience_mod.preview(self.business, request.data.get("definition") or {}))


# ---------------------------------------------------------------------------
# Saved audiences
# ---------------------------------------------------------------------------


class AudienceSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Audience
        fields = [
            "id",
            "business_id",
            "name",
            "description",
            "definition",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AudienceListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Audience
    business_field = "business"
    serializer_class = AudienceSerializer
    pagination_class = None  # bounded per business
    required_permissions = {"get": "campaigns.view", "post": "campaigns.manage"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return queryset.order_by("name")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if self.business is None:
            raise BusinessRequiredError()
        audience = services.save_audience(
            business=self.business, actor=request.user, data=dict(request.data), request=request
        )
        return Response(AudienceSerializer(audience).data, status=status.HTTP_201_CREATED)


class AudienceDetailView(OrganizationScopedMixin, APIView):
    required_permissions = {
        "get": "campaigns.view",
        "patch": "campaigns.manage",
        "delete": "campaigns.manage",
    }

    def _audience(self, pk: Any) -> Audience:
        audience = (
            self.restrict_to_businesses(
                Audience.objects.for_organization(self.organization).select_related(
                    "business__organization"
                ),
                "business",
            )
            .filter(pk=pk)
            .first()
        )
        if audience is None:
            raise NotFoundError("audience not found")
        return audience

    def get(self, request: Request, pk: str) -> Response:
        audience = self._audience(pk)
        data = AudienceSerializer(audience).data
        data["preview"] = audience_mod.preview(
            audience.business, audience.definition, sample_size=0
        )
        return Response(data)

    def patch(self, request: Request, pk: str) -> Response:
        audience = self._audience(pk)
        saved = services.save_audience(
            business=audience.business,
            actor=request.user,
            data=dict(request.data),
            audience=audience,
            request=request,
        )
        return Response(AudienceSerializer(saved).data)

    def delete(self, request: Request, pk: str) -> Response:
        services.delete_audience(self._audience(pk), actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
