"""In-app notifications for members (schema only in this phase)."""

from django.db import models

from apps.organizations.tenancy import TenantScopedModel


class Notification(TenantScopedModel):
    recipient = models.ForeignKey(
        "organizations.Membership", on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=64)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["recipient", "read_at", "-created_at"], name="notification_inbox_idx"
            ),
        ]
