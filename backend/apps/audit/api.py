from typing import Any

from django.db.models import QuerySet
from rest_framework import generics, serializers

from apps.audit.models import AuditLog
from apps.organizations.api.scoping import OrganizationScopedMixin


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor_type",
            "actor_email",
            "action",
            "resource_type",
            "resource_id",
            "metadata",
            "ip_address",
            "request_id",
            "created_at",
        ]
        read_only_fields = fields


class AuditLogListView(OrganizationScopedMixin, generics.ListAPIView):
    serializer_class = AuditLogSerializer
    required_permissions = {"get": "audit.view"}

    def get_queryset(self) -> QuerySet[Any]:
        # AuditLog allows organization=NULL (platform events), so it does not
        # use TenantScopedModel; scope explicitly.
        queryset = AuditLog.objects.filter(organization=self.organization).select_related("actor")
        action = self.request.query_params.get("action")
        if action:
            queryset = queryset.filter(action=action)
        return queryset
