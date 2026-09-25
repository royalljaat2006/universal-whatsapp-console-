from django.urls import path

from apps.campaigns import api

urlpatterns = [
    path("", api.AudienceListCreateView.as_view(), name="audience-list"),
    path("<uuid:pk>/", api.AudienceDetailView.as_view(), name="audience-detail"),
]
