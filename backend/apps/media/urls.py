from django.urls import path

from apps.media import api

urlpatterns = [
    path("", api.MediaUploadView.as_view(), name="media-upload"),
    path("<uuid:pk>/", api.MediaAssetDetailView.as_view(), name="media-detail"),
    path("<uuid:pk>/content/", api.MediaContentView.as_view(), name="media-content"),
]
