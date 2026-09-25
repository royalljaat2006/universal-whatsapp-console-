"""Conversations.

A conversation is the continuous thread between ONE business phone number and
ONE contact (the inbox unit). It is opened/resolved by agents; it is not
Meta's billing "conversation" (that concept was replaced by per-message
pricing on 2025-07-01 and lives only in billing data).
"""

from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.organizations.tenancy import TenantScopedModel


class Conversation(TenantScopedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PENDING = "pending", "Pending"
        RESOLVED = "resolved", "Resolved"
        ARCHIVED = "archived", "Archived"

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="conversations"
    )

    phone_number = models.ForeignKey(
        "whatsapp.WhatsAppPhoneNumber", on_delete=models.PROTECT, related_name="conversations"
    )
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.PROTECT, related_name="conversations"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    assigned_membership = models.ForeignKey(
        "organizations.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_conversations",
    )
    assigned_team = models.ForeignKey(
        "teams.Team", on_delete=models.SET_NULL, null=True, blank=True, related_name="conversations"
    )
    last_message_at = models.DateTimeField(null=True, blank=True)
    # Drives the customer service window (free-form replies allowed).
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_message_preview = models.CharField(max_length=200, blank=True)
    last_message_direction = models.CharField(max_length=8, choices=Direction.choices, blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    unread_count = models.PositiveIntegerField(default=0)
    tags = models.ManyToManyField("contacts.Tag", related_name="conversations", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "phone_number", "contact"],
                name="conversation_thread_unique",
            ),
        ]
        indexes = [
            # Inbox list inside one business: newest activity first,
            # optionally per number / status.
            models.Index(
                fields=["business", "-last_message_at"], name="conv_business_last_msg_idx"
            ),
            models.Index(
                fields=["business", "status", "-last_message_at"],
                name="conv_business_status_last_idx",
            ),
            models.Index(
                fields=["phone_number", "-last_message_at"], name="conv_phone_last_msg_idx"
            ),
            # Organization-wide view for all-business members.
            models.Index(fields=["organization", "-last_message_at"], name="conv_org_last_msg_idx"),
            # "My conversations" for an agent.
            models.Index(fields=["assigned_membership", "status"], name="conv_assignee_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.contact_id} <-> {self.phone_number_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # A conversation belongs to its number's business, always.
        self.business_id = self.phone_number.business_id
        super().save(*args, **kwargs)

    def service_window_expires_at(self) -> datetime | None:
        if self.last_inbound_at is None:
            return None
        return self.last_inbound_at + timedelta(hours=settings.WHATSAPP_SERVICE_WINDOW_HOURS)

    def is_service_window_open(self, now: datetime | None = None) -> bool:
        expires = self.service_window_expires_at()
        return expires is not None and (now or timezone.now()) < expires


class ConversationAssignment(TenantScopedModel):
    """Append-only assignment history (who had it, who has it, who changed it)."""

    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="+")
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="assignments"
    )
    previous_membership = models.ForeignKey(
        "organizations.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    previous_team = models.ForeignKey(
        "teams.Team", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    new_membership = models.ForeignKey(
        "organizations.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    new_team = models.ForeignKey(
        "teams.Team", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["conversation", "-created_at"], name="conv_assignment_idx"),
        ]
