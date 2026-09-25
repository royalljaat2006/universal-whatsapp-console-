"""Outbound messaging policy — the single place that decides what may be sent.

Verified rules (Meta "Send messages" / "Pricing", 2026-09-23):
- A customer service window of 24 hours starts when the WhatsApp user
  messages (or calls) the business and resets on each new user message/call.
- Inside the window any service (non-template) message may be sent.
- Outside the window only approved template messages may be sent.

Known gap (documented): user *calls* also open the window, but calling
webhooks are not ingested yet, so the platform may conservatively require a
template after a call-only interaction. It never allows more than Meta does.

If Meta changes these rules, only this module changes.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.conversations.models import Conversation

FREE_FORM = "free_form"  # text, media, reactions, interactive
TEMPLATE = "template"


@dataclass(frozen=True)
class SendPolicy:
    free_form_allowed: bool
    template_allowed: bool
    window_open: bool
    window_expires_at: datetime | None
    reason: str  # machine code for the UI

    def allows(self, kind: str) -> bool:
        return self.template_allowed if kind == TEMPLATE else self.free_form_allowed

    def as_dict(self) -> dict[str, object]:
        return {
            "free_form_allowed": self.free_form_allowed,
            "template_allowed": self.template_allowed,
            "window_open": self.window_open,
            "window_expires_at": (
                self.window_expires_at.isoformat() if self.window_expires_at else None
            ),
            "reason": self.reason,
        }


def evaluate(conversation: Conversation, *, now: datetime | None = None) -> SendPolicy:
    now = now or timezone.now()
    if conversation.last_inbound_at is None:
        return SendPolicy(False, True, False, None, "TEMPLATE_REQUIRED_NO_CUSTOMER_MESSAGE")
    expires = conversation.last_inbound_at + timedelta(hours=settings.WHATSAPP_SERVICE_WINDOW_HOURS)
    if now < expires:
        return SendPolicy(True, True, True, expires, "SERVICE_WINDOW_OPEN")
    return SendPolicy(False, True, False, expires, "TEMPLATE_REQUIRED_WINDOW_CLOSED")
