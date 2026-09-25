from django.urls import path

from apps.apikeys import api

urlpatterns = [
    path("", api.ApiKeyListCreateView.as_view(), name="api-key-list"),
    path("<uuid:pk>/", api.ApiKeyRevokeView.as_view(), name="api-key-revoke"),
]
