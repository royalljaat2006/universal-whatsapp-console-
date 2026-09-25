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
]
