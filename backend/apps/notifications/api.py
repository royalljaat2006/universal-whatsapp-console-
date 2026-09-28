"""Notifications API (organization-scoped, always personal to the caller's
own membership — there is no endpoint to read another member's inbox).

GET  /api/v1/notifications/                 list, newest first (notifications.view)
     ?unread=true                           only unread
GET  /api/v1/notifications/unread-count/    {"unread_count": N} (notifications.view)
POST /api/v1/notifications/<id>/read/       mark one read (notifications.manage)
POST /api/v1/notifications/read-all/        mark all read (notifications.manage)
GET  /api/v1/notifications/preferences/     {"muted_kinds": [...], "available_kinds": {...}}
PATCH /api/v1/notifications/preferences/    {"muted_kinds": [...]} (notifications.manage)
"""

from typing import Any

from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.services import KINDS
from apps.organizations.api.scoping import OrganizationScopedMixin


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "data", "read_at", "created_at"]
        read_only_fields = fields


class NotificationListView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Notification
    serializer_class = NotificationSerializer
    required_permissions = {"get": "notifications.view"}

    def scope_queryset(self, queryset: Any) -> Any:
        queryset = queryset.filter(recipient=self.membership).order_by("-created_at")
        if self.request.query_params.get("unread") == "true":
            queryset = queryset.filter(read_at__isnull=True)
        return queryset


class NotificationUnreadCountView(OrganizationScopedMixin, APIView):
    required_permissions = {"get": "notifications.view"}

    def get(self, request: Request) -> Response:
        count = Notification.objects.for_organization(self.organization).filter(
            recipient=self.membership, read_at__isnull=True
        ).count()
        return Response({"unread_count": count})


class NotificationMarkReadView(OrganizationScopedMixin, APIView):
    required_permissions = {"post": "notifications.manage"}

    def post(self, request: Request, pk: str) -> Response:
        notification = (
            Notification.objects.for_organization(self.organization)
            .filter(pk=pk, recipient=self.membership)
            .first()
        )
        if notification is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])
        return Response(NotificationSerializer(notification).data)


class NotificationMarkAllReadView(OrganizationScopedMixin, APIView):
    required_permissions = {"post": "notifications.manage"}

    def post(self, request: Request) -> Response:
        count = (
            Notification.objects.for_organization(self.organization)
            .filter(recipient=self.membership, read_at__isnull=True)
            .update(read_at=timezone.now())
        )
        return Response({"marked_read": count})


class NotificationPreferenceView(OrganizationScopedMixin, APIView):
    queryset_model = NotificationPreference
    required_permissions = {"get": "notifications.view", "patch": "notifications.manage"}

    def _preference(self) -> NotificationPreference:
        preference, _ = NotificationPreference.objects.get_or_create(
            organization=self.organization, membership=self.membership
        )
        return preference

    def get(self, request: Request) -> Response:
        preference = self._preference()
        return Response({"muted_kinds": preference.muted_kinds, "available_kinds": KINDS})

    def patch(self, request: Request) -> Response:
        muted = request.data.get("muted_kinds")
        if not isinstance(muted, list) or not all(isinstance(k, str) for k in muted):
            raise serializers.ValidationError({"muted_kinds": "Must be a list of kind codes."})
        unknown = set(muted) - set(KINDS)
        if unknown:
            raise serializers.ValidationError({"muted_kinds": f"Unknown kinds: {sorted(unknown)}"})
        preference = self._preference()
        preference.muted_kinds = sorted(set(muted))
        preference.save(update_fields=["muted_kinds", "updated_at"])
        return Response({"muted_kinds": preference.muted_kinds, "available_kinds": KINDS})
