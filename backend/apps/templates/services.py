"""Template synchronisation, creation and status tracking."""

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.templates.models import MessageTemplate, MessageTemplateVersion
from apps.whatsapp.models import WhatsAppBusinessAccount
from apps.whatsapp.services import account_ref, provider_for
from core.exceptions import TemplateCreateError
from providers.whatsapp.errors import ProviderError
from providers.whatsapp.types import TemplateInfo, TemplateSpec, TemplateStatusEvent

logger = logging.getLogger(__name__)


def _content_hash(category: str, status: str, components: list[dict]) -> str:
    raw = json.dumps([category, status, components], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _snapshot(template: MessageTemplate) -> None:
    digest = _content_hash(template.category, template.status, template.components)
    latest = template.versions.order_by("-version").first()
    if latest and latest.content_hash == digest:
        return
    next_version = (template.versions.aggregate(v=Max("version"))["v"] or 0) + 1
    MessageTemplateVersion.objects.create(
        organization_id=template.organization_id,
        template=template,
        version=next_version,
        category=template.category,
        status=template.status,
        components=template.components,
        content_hash=digest,
    )


@transaction.atomic
def upsert_template(account: WhatsAppBusinessAccount, info: TemplateInfo) -> MessageTemplate:
    template, _ = MessageTemplate.objects.select_for_update().get_or_create(
        business_account=account,
        name=info.name,
        language=info.language,
        defaults={
            "organization_id": account.organization_id,
            "category": info.category,
            "status": info.status,
        },
    )
    template.external_id = info.external_id
    template.category = info.category
    template.status = info.status
    template.components = info.components
    template.parameter_format = info.parameter_format
    template.quality_score = info.quality_score
    template.rejected_reason = info.rejected_reason[:255]
    template.last_synced_at = timezone.now()
    template.save()
    _snapshot(template)
    return template


def create_template(
    account: WhatsAppBusinessAccount,
    *,
    name: str,
    language: str,
    category: str,
    components: list[dict[str, Any]],
    parameter_format: str | None = None,
    actor: Any = None,
) -> MessageTemplate:
    """Submit a new template to Meta for review and store the result
    (status PENDING until Meta's review webhook or the next sync updates
    it). Meta owns validation — a rejected name/components combination
    surfaces as ``TemplateCreateError`` with Meta's own reason."""
    spec = TemplateSpec(
        name=name,
        language=language,
        category=category,
        components=components,
        parameter_format=parameter_format,
    )
    try:
        info = provider_for(account).create_template(account_ref(account), spec)
    except ProviderError as exc:
        raise TemplateCreateError(exc.message, details={"provider_code": exc.provider_code}) from exc
    template = upsert_template(account, info)
    audit(
        actions.TEMPLATE_CREATED,
        organization=account.organization,
        actor=actor,
        resource=template,
        metadata={"name": name, "language": language, "category": category},
    )
    return template


def sync_templates(account: WhatsAppBusinessAccount) -> int:
    """Pull every template of the WABA from the provider (read-only at the
    provider). Templates no longer returned are flagged
    ``missing_from_provider`` (never deleted: messages reference them).
    Sync state and errors are recorded on the WABA."""
    from apps.whatsapp.models import SyncState

    WhatsAppBusinessAccount.objects.filter(pk=account.pk).update(
        template_sync_state=SyncState.SYNCING, updated_at=timezone.now()
    )
    provider = provider_for(account)
    seen: list[str] = []
    try:
        for info in provider.list_templates(account_ref(account)):
            template = upsert_template(account, info)
            seen.append(str(template.pk))
    except Exception as exc:
        WhatsAppBusinessAccount.objects.filter(pk=account.pk).update(
            template_sync_state=SyncState.FAILED,
            template_sync_error=f"{getattr(exc, 'code', type(exc).__name__)}: {getattr(exc, 'message', exc)}"[
                :500
            ],
            updated_at=timezone.now(),
        )
        raise
    MessageTemplate.objects.filter(business_account=account).exclude(pk__in=seen).update(
        missing_from_provider=True, updated_at=timezone.now()
    )
    MessageTemplate.objects.filter(pk__in=seen).update(missing_from_provider=False)
    WhatsAppBusinessAccount.objects.filter(pk=account.pk).update(
        template_sync_state=SyncState.SUCCEEDED,
        template_sync_error="",
        templates_last_synced_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return len(seen)


@transaction.atomic
def apply_template_status_event(
    account: WhatsAppBusinessAccount, event: TemplateStatusEvent
) -> MessageTemplate | None:
    """Apply a status webhook. Events older than the last applied one are
    ignored (webhook order is not chronological)."""
    template = (
        MessageTemplate.objects.select_for_update()
        .filter(business_account=account, external_id=event.template_external_id)
        .first()
    )
    if template is None:
        logger.info(
            "status event for unknown template; will appear on next sync",
            extra={"template_external_id": event.template_external_id},
        )
        return None
    occurred_at: datetime = event.occurred_at or timezone.now()
    if template.last_status_event_at and occurred_at < template.last_status_event_at:
        return template
    template.status = event.event
    if event.category:
        template.category = event.category
    if event.reason and event.reason != "NONE":
        template.rejected_reason = event.reason[:255]
    template.last_status_event_at = occurred_at
    template.save()
    _snapshot(template)
    return template
