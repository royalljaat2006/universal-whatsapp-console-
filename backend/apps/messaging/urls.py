from django.urls import path

from apps.messaging import api

urlpatterns = [
    path("", api.MessageListCreateView.as_view(), name="message-list"),
    path("<uuid:pk>/", api.MessageDetailView.as_view(), name="message-detail"),
]
