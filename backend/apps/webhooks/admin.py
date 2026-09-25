from typing import Any

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.audit import actions
from apps.audit.services import record as audit
from apps.webhooks.models import WebhookEvent, WebhookProcessingAttempt
from apps.webhooks.processor import mark_for_replay
from apps.webhooks.tasks import process_webhook_event


class AttemptInline(admin.TabularInline):
    model = WebhookProcessingAttempt
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in WebhookProcessingAttempt._meta.fields]

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "status", "fields", "attempts", "received_at", "processed_at")
    list_filter = ("provider", "status")
    search_fields = (
        "id",
        "body_sha256",
        "phone_number_external_ids",
        "business_account_external_ids",
    )
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]
    inlines = [AttemptInline]
    actions = ["replay"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.action(description="Replay selected events through the processor")
    def replay(self, request: HttpRequest, queryset: QuerySet[WebhookEvent]) -> None:
        for event in queryset:
            mark_for_replay(event)
            process_webhook_event.delay(str(event.pk), trigger="replay")
            audit(actions.WEBHOOK_REPLAYED, actor=request.user, resource=event, request=request)
        self.message_user(
            request, f"{queryset.count()} event(s) queued for replay", messages.SUCCESS
        )
