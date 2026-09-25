from django.urls import path

from apps.businesses import api

urlpatterns = [
    path("", api.BusinessListCreateView.as_view(), name="business-list"),
    path("<uuid:pk>/", api.BusinessDetailView.as_view(), name="business-detail"),
]
