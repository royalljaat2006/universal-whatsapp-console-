from django.urls import path

from apps.platform import api

urlpatterns = [
    path("organizations/", api.OrganizationDirectoryView.as_view(), name="platform-organizations"),
    path(
        "organizations/<uuid:pk>/",
        api.OrganizationAdminDetailView.as_view(),
        name="platform-organization-detail",
    ),
    path("audit-logs/", api.PlatformAuditLogListView.as_view(), name="platform-audit-logs"),
    path("operations/", api.OperationsSummaryView.as_view(), name="platform-operations"),
    path("failed-jobs/", api.PlatformFailedJobsView.as_view(), name="platform-failed-jobs"),
    path("backups/", api.BackupHistoryView.as_view(), name="platform-backups"),
    path(
        "webhook-events/", api.PlatformWebhookEventListView.as_view(), name="platform-webhook-events"
    ),
    path(
        "webhook-events/<uuid:pk>/",
        api.PlatformWebhookEventDetailView.as_view(),
        name="platform-webhook-event-detail",
    ),
    path(
        "webhook-events/<uuid:pk>/replay/",
        api.PlatformWebhookReplayView.as_view(),
        name="platform-webhook-event-replay",
    ),
]
