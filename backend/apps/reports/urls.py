from django.urls import path

from apps.reports import api

urlpatterns = [
    path("", api.ExportListCreateView.as_view(), name="export-list"),
    path("<uuid:pk>/", api.ExportDetailView.as_view(), name="export-detail"),
    path("<uuid:pk>/download/", api.ExportDownloadView.as_view(), name="export-download"),
]
