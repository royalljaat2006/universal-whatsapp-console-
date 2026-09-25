"""Account / number notifications from the provider (verified 2026-09-23).

Handled fields and the documented values we interpret:
- ``phone_number_quality_update``: ``display_phone_number``, ``event``
  (e.g. ONBOARDING, THROUGHPUT_UPGRADE), ``current_limit`` / ``old_limit``
  (TIER_50 … TIER_UNLIMITED), ``max_daily_conversations_per_business``.
  Numbers are identified by display number (no phone number id is sent).
- ``account_alerts``: ``entity_type``, ``entity_id``, ``alert_info``
  {``alert_severity`` CRITICAL|WARNING|INFORMATIONAL, ``alert_status``,
  ``alert_type``, ``alert_description``}.
- ``account_update``: ``event`` (ACCOUNT_RESTRICTION, ACCOUNT_DELETED,
  ACCOUNT_OFFBOARDED, ACCOUNT_RECONNECTED, PARTNER_REMOVED, DISABLED_UPDATE,
  …) plus event-specific ``*_info`` objects kept verbatim.
- ``business_capability_update``: ``max_daily_conversations_per_business``,
  ``max_phone_numbers_per_waba`` (and conditionally others).
Any other field is recorded verbatim with no interpretation. Nothing here
assumes an undocumented payload shape; unknown values never raise.
"""

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.messaging.services import events
from apps.whatsapp.models import (
    ConnectionState,
    WhatsAppAccountEvent,
    WhatsAppBusinessAccount,
    WhatsAppPhoneNumber,
)

Severity = WhatsAppAccountEvent.Severity

ALERT_SEVERITY = {
    "CRITICAL": Severity.CRITICAL,
    "WARNING": Severity.WARNING,
    "INFORMATIONAL": Severity.INFO,
}
CRITICAL_ACCOUNT_EVENTS = {
    "ACCOUNT_DELETED",
    "ACCOUNT_OFFBOARDED",
    "ACCOUNT_RESTRICTION",
    "ACCOUNT_VIOLATION",
    "DISABLED_UPDATE",
}
WARNING_ACCOUNT_EVENTS = {"PARTNER_REMOVED", "PARTNER_APP_UNINSTALLED"}
DISCONNECTING_EVENTS = {"ACCOUNT_DELETED", "ACCOUNT_OFFBOARDED", "PARTNER_REMOVED"}


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _number_by_display(
    account: WhatsAppBusinessAccount, display: Any
) -> WhatsAppPhoneNumber | None:
    wanted = _digits(display)
    if not wanted:
        return None
    for number in account.phone_numbers.all():
        if _digits(number.display_phone_number) == wanted:
            return number
    return None


def _dedupe_key(
    account: WhatsAppBusinessAccount, field: str, value: dict[str, Any], at: datetime | None
) -> str:
    raw = json.dumps(
        [str(account.pk), field, value, at.isoformat() if at else None], sort_keys=True, default=str
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@transaction.atomic
def handle_account_event(
    account: WhatsAppBusinessAccount,
    *,
    field: str,
    value: dict[str, Any],
    occurred_at: datetime | None,
    source_event_id: UUID | None = None,
) -> WhatsAppAccountEvent | None:
    """Record the notification and apply documented side effects. Returns
    None for a duplicate delivery/replay (already recorded)."""
    key = _dedupe_key(account, field, value, occurred_at)
    if WhatsAppAccountEvent.objects.filter(dedupe_key=key).exists():
        return None

    number: WhatsAppPhoneNumber | None = None
    event_name = str(value.get("event") or "")
    severity = Severity.INFO
    summary = ""
    number_changed = False

    if field == "phone_number_quality_update":
        number = _number_by_display(account, value.get("display_phone_number"))
        current = str(value.get("current_limit") or "")
        old = str(value.get("old_limit") or "")
        summary = f"{event_name or 'update'}: messaging limit {old + ' → ' if old else ''}{current}".strip()
        if number is not None and current and number.messaging_limit_tier != current:
            number.messaging_limit_tier = current
            number.provider_metadata = {
                **number.provider_metadata,
                "last_quality_event": {
                    "event": event_name,
                    "current_limit": current,
                    "old_limit": old,
                },
            }
            number.save(update_fields=["messaging_limit_tier", "provider_metadata", "updated_at"])
            number_changed = True
    elif field == "account_alerts":
        info = value.get("alert_info") or {}
        event_name = str(info.get("alert_type") or "")
        severity = ALERT_SEVERITY.get(str(info.get("alert_severity") or "").upper(), Severity.INFO)
        summary = str(info.get("alert_description") or event_name)[:500]
        if str(value.get("entity_type") or "").upper() == "PHONE_NUMBER":
            entity = str(value.get("entity_id") or "")
            number = account.phone_numbers.filter(external_id=entity).first() or _number_by_display(
                account, entity
            )
    elif field == "account_update":
        if event_name in CRITICAL_ACCOUNT_EVENTS:
            severity = Severity.CRITICAL
        elif event_name in WARNING_ACCOUNT_EVENTS:
            severity = Severity.WARNING
        summary = f"Account update: {event_name}"
        if event_name == "ACCOUNT_RESTRICTION":
            account.status = WhatsAppBusinessAccount.Status.RESTRICTED
            account.save(update_fields=["status", "updated_at"])
        elif event_name in DISCONNECTING_EVENTS:
            account.connection_state = ConnectionState.DISCONNECTED
            account.connection_error = f"Provider reported {event_name}"
            account.save(update_fields=["connection_state", "connection_error", "updated_at"])
        elif event_name == "ACCOUNT_RECONNECTED":
            account.connection_state = ConnectionState.PENDING
            account.save(update_fields=["connection_state", "updated_at"])
    elif field == "business_capability_update":
        summary = "Business capabilities updated"
        account.provider_metadata = {**account.provider_metadata, "capabilities": value}
        account.save(update_fields=["provider_metadata", "updated_at"])
    else:
        summary = f"{field}{': ' + event_name if event_name else ''}"

    try:
        with transaction.atomic():
            record = WhatsAppAccountEvent.objects.create(
                organization_id=account.organization_id,
                business_id=account.business_id,
                business_account=account,
                phone_number=number,
                field=field[:64],
                event=event_name[:100],
                severity=severity,
                summary=summary[:500],
                payload=value,
                occurred_at=occurred_at or timezone.now(),
                dedupe_key=key,
                source_event_id=source_event_id,
            )
    except IntegrityError:
        return None  # concurrent duplicate
    if number_changed and number is not None:
        events.number_status_changed(number)
    if severity != Severity.INFO or field == "account_update":
        events.account_alert(record)
    return record
