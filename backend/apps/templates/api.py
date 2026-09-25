"""Templates API (business/WABA-scoped).

GET  /api/v1/templates/?q=&status=&category=&language=&business_account=&sendable=1
GET  /api/v1/templates/<id>/          includes the server-derived parameter list
POST /api/v1/templates/sync/          queue a sync of every WABA of the selected business
"""

from dataclasses import asdict
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.templates.models import MessageTemplate
from apps.templates.parameters import UnsupportedTemplateError, describe
from apps.whatsapp.models import WhatsAppBusinessAccount
from core.exceptions import DomainError


class MessageTemplateSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    business_account_id = serializers.UUIDField(read_only=True)
    business_account_external_id = serializers.CharField(
        source="business_account.external_id", read_only=True
    )
    sendable = serializers.SerializerMethodField()

    class Meta:
        model = MessageTemplate
        fields = [
            "id",
            "business_id",
            "business_account_id",
            "business_account_external_id",
            "external_id",
            "name",
            "language",
            "category",
            "status",
            "sendable",
            "parameter_format",
            "quality_score",
            "rejected_reason",
            "missing_from_provider",
            "last_synced_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_sendable(self, obj: MessageTemplate) -> bool:
        return obj.is_sendable and not obj.missing_from_provider


class MessageTemplateDetailSerializer(MessageTemplateSerializer):
    components = serializers.JSONField(read_only=True)
    structure = serializers.SerializerMethodField()

    class Meta(MessageTemplateSerializer.Meta):
        fields = [*MessageTemplateSerializer.Meta.fields, "components", "structure"]
        read_only_fields = fields

    def get_structure(self, obj: MessageTemplate) -> dict[str, Any]:
        try:
            shape = describe(obj)
        except UnsupportedTemplateError as exc:
            return {"supported": False, "reason": str(exc)}
        return {
            "supported": True,
            "parameter_format": shape.parameter_format,
            "header_format": shape.header_format,
            "header_text": shape.header_text,
            "body_text": shape.body_text,
            "footer_text": shape.footer_text,
            "buttons": shape.buttons,
            "parameters": [asdict(p) for p in shape.parameters],
        }


class MessageTemplateListView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = MessageTemplate
    business_field = "business"
    serializer_class = MessageTemplateSerializer
    required_permissions = {"get": "templates.view"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        params = self.request.query_params
        if params.get("business_account"):
            queryset = queryset.filter(business_account_id=params["business_account"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"].upper())
        if params.get("category"):
            queryset = queryset.filter(category=params["category"].upper())
        if params.get("language"):
            queryset = queryset.filter(language=params["language"])
        if params.get("sendable") in {"1", "true"}:
            queryset = queryset.filter(status="APPROVED", missing_from_provider=False)
        q = (params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(name__icontains=q)
        return queryset.select_related("business_account")


class MessageTemplateDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    queryset_model = MessageTemplate
    business_field = "business"
    serializer_class = MessageTemplateDetailSerializer
    required_permissions = {"get": "templates.view"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return queryset.select_related("business_account")


class TemplateSyncView(OrganizationScopedMixin, APIView):
    required_permissions = {"post": "templates.manage"}

    def post(self, request: Request) -> Response:
        from apps.templates.tasks import sync_business_account_templates

        if self.business is None:
            raise DomainError("Select a business (X-Business-ID) to sync its templates.")
        ids = [
            str(pk)
            for pk in WhatsAppBusinessAccount.objects.filter(business=self.business).values_list(
                "pk", flat=True
            )
        ]

        def queue(account_id: str) -> None:
            transaction.on_commit(lambda: sync_business_account_templates.delay(account_id))

        for account_id in ids:
            queue(account_id)
        return Response({"status": "queued", "accounts": len(ids)}, status=status.HTTP_202_ACCEPTED)
