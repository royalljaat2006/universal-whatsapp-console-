from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "actor", "action", "resource_type", "resource_id")
    list_filter = ("action", "actor_type")
    search_fields = ("action", "resource_id", "request_id")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
