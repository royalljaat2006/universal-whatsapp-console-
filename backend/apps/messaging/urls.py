from django.urls import path

from apps.messaging import api

urlpatterns = [
    path("", api.MessageListCreateView.as_view(), name="message-list"),
    path("failures/", api.FailedMessagesView.as_view(), name="message-failures"),
    path("<uuid:pk>/", api.MessageDetailView.as_view(), name="message-detail"),
    path("<uuid:pk>/recovery/", api.MessageRecoveryView.as_view(), name="message-recovery"),
    path("<uuid:pk>/retry/", api.MessageRetryView.as_view(), name="message-retry"),
]
