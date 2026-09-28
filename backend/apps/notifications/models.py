"""In-app notifications for members, and their per-member preferences.

Rows are created only through apps.notifications.services.notify — never
directly by other apps — so muting and realtime fan-out stay consistent."""

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


class NotificationPreference(TenantScopedModel):
    """One row per membership. ``muted_kinds`` lists notification kind codes
    (apps.notifications.services.KINDS) the member does not want to receive;
    an empty list (the default) means every kind is delivered."""

    membership = models.OneToOneField(
        "organizations.Membership", on_delete=models.CASCADE, related_name="notification_preference"
    )
    muted_kinds = models.JSONField(default=list, blank=True)
