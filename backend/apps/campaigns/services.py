"""Campaign and audience management (drafts). Execution lives in engine.py.

Every id in the payload (number, template, audience, tags, media) is checked
against the campaign's business, which itself comes from the validated
X-Business-ID context — never from the request body.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.audit import actions
from apps.audit.services import record as audit
from apps.businesses.models import Business
from apps.campaigns import audience as audience_mod
from apps.campaigns import variables
from apps.campaigns.engine import CampaignStateError, record_event
from apps.campaigns.models import Audience, Campaign
from apps.templates.models import MessageTemplate
from apps.templates.parameters import UnsupportedTemplateError, describe
from apps.whatsapp.models import WhatsAppPhoneNumber
from core.exceptions import ConflictError, DomainError, NotFoundError


class CampaignValidationError(DomainError):
    code = "CAMPAIGN_INVALID"
    status_code = 422
    default_message = "The campaign is invalid."


EDITABLE = {
    "name",
    "description",
    "phone_number_id",
    "template_id",
    "audience_id",
    "audience_definition",
    "variable_mapping",
    "max_messages_per_minute",
    "scheduled_at",
    "timezone",
    "metadata",
}


def _number(business: Business, number_id: Any) -> WhatsAppPhoneNumber:
    number = (
        WhatsAppPhoneNumber.objects.select_related("business_account")
        .filter(pk=number_id, business=business)
        .first()
        if number_id
        else None
    )
    if number is None:
        raise NotFoundError("phone number not found in this business")
    return number


def _template(number: WhatsAppPhoneNumber, template_id: Any) -> MessageTemplate:
    template = (
        MessageTemplate.objects.filter(
            pk=template_id, business_account_id=number.business_account_id
        ).first()
        if template_id
        else None
    )
    if template is None:
        raise NotFoundError("template not found for this number's WhatsApp Business Account")
    if not template.is_sendable or template.missing_from_provider:
        raise CampaignValidationError(
            "Only approved templates can be used.", details={"template_status": template.status}
        )
    try:
        describe(template)
    except UnsupportedTemplateError as exc:
        raise CampaignValidationError(str(exc)) from exc
    return template


def _schedule(raw: Any, tz_name: str) -> datetime | None:
    if raw in (None, ""):
        return None
    value = parse_datetime(str(raw))
    if value is None:
        raise CampaignValidationError(details={"scheduled_at": "invalid datetime (ISO 8601)"})
    if timezone.is_naive(value):
        try:
            value = value.replace(tzinfo=ZoneInfo(tz_name or "UTC"))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CampaignValidationError(details={"timezone": "unknown time zone"}) from exc
    return value


def _timezone(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CampaignValidationError(details={"timezone": "unknown time zone"}) from exc
    return name


def _rate(raw: Any, number: WhatsAppPhoneNumber) -> int:
    try:
        rate = int(raw)
    except (TypeError, ValueError) as exc:
        raise CampaignValidationError(details={"max_messages_per_minute": "whole number"}) from exc
    maximum = min(settings.CAMPAIGN_MAX_RATE_PER_MINUTE, int(number.effective_throughput_mps * 60))
    if not 1 <= rate <= maximum:
        raise CampaignValidationError(
            details={"max_messages_per_minute": f"must be between 1 and {maximum}"}
        )
    return rate


def _apply(campaign: Campaign, business: Business, data: dict[str, Any]) -> list[str]:
    """Validate and apply ``data``; returns the names of changed groups."""
    unknown = set(data) - EDITABLE
    if unknown:
        raise CampaignValidationError(details={k: "not editable" for k in sorted(unknown)})
    changed: list[str] = []
    if "name" in data:
        name = str(data["name"] or "").strip()
        if not 1 <= len(name) <= 200:
            raise CampaignValidationError(details={"name": "1-200 characters required"})
        campaign.name = name
    if "description" in data:
        campaign.description = str(data["description"] or "")[:1000]
    if "metadata" in data:
        if not isinstance(data["metadata"], dict):
            raise CampaignValidationError(details={"metadata": "must be an object"})
        campaign.metadata = data["metadata"]

    number_id = data.get("phone_number_id", getattr(campaign, "phone_number_id", None))
    number = _number(business, number_id)
    template_id = data.get("template_id", getattr(campaign, "template_id", None))
    template = _template(number, template_id)
    if (
        campaign.pk is None
        or template.pk != campaign.template_id
        or number.pk != campaign.phone_number_id
    ):
        changed.append("template")
    campaign.phone_number = number
    campaign.template = template

    if "audience_id" in data:
        audience_id = data["audience_id"]
        if audience_id:
            saved = Audience.objects.filter(pk=audience_id, business=business).first()
            if saved is None:
                raise NotFoundError("audience not found in this business")
            campaign.audience = saved
            if "audience_definition" not in data:
                campaign.audience_definition = saved.definition
                changed.append("audience")
        else:
            campaign.audience = None
    if "audience_definition" in data:
        campaign.audience_definition = audience_mod.validate_definition(
            business, data["audience_definition"]
        )
        changed.append("audience")
    elif campaign.pk is None and not campaign.audience_definition:
        campaign.audience_definition = {"match": "all", "conditions": []}

    if "variable_mapping" in data or "template" in changed:
        mapping = data.get("variable_mapping", campaign.variable_mapping) or {}
        campaign.variable_mapping = (
            variables.validate_mapping(template, business, mapping) if mapping else {}
        )
    if "max_messages_per_minute" in data:
        campaign.max_messages_per_minute = _rate(data["max_messages_per_minute"], number)
    elif campaign.pk is None:
        campaign.max_messages_per_minute = _rate(settings.CAMPAIGN_DEFAULT_RATE_PER_MINUTE, number)
    if "timezone" in data:
        campaign.timezone = _timezone(data["timezone"])
    if "scheduled_at" in data:
        campaign.scheduled_at = _schedule(data["scheduled_at"], campaign.timezone)
        changed.append("schedule")
    return changed


def create_campaign(
    *, business: Business, data: dict[str, Any], actor: Any, request: Any = None
) -> Campaign:
    campaign = Campaign(
        organization_id=business.organization_id,
        business=business,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    with transaction.atomic():
        _apply(campaign, business, data)
        if not campaign.name:
            raise CampaignValidationError(details={"name": "required"})
        campaign.save()
        record_event(campaign, "created", actor)
        audit(
            actions.CAMPAIGN_CREATED,
            organization=business.organization,
            actor=actor,
            resource=campaign,
            metadata={"business_id": str(business.pk), "template_id": str(campaign.template_id)},
            request=request,
        )
    return campaign


def update_campaign(
    campaign: Campaign, *, data: dict[str, Any], actor: Any, request: Any = None
) -> Campaign:
    with transaction.atomic():
        locked = Campaign.objects.select_for_update().get(pk=campaign.pk)
        if not locked.is_editable:
            raise CampaignStateError("Only draft campaigns can be edited.")
        changed = _apply(locked, locked.business, data)
        locked.save()
        record_event(locked, "updated", actor, fields=sorted(data))
        organization = locked.business.organization
        meta = {"business_id": str(locked.business_id), "fields": sorted(data)}
        audit(
            actions.CAMPAIGN_UPDATED,
            organization=organization,
            actor=actor,
            resource=locked,
            metadata=meta,
            request=request,
        )
        if "audience" in changed:
            audit(
                actions.CAMPAIGN_AUDIENCE_CHANGED,
                organization=organization,
                actor=actor,
                resource=locked,
                metadata={**meta, "audience": locked.audience_definition},
                request=request,
            )
        if "template" in changed and "template_id" in data:
            audit(
                actions.CAMPAIGN_TEMPLATE_CHANGED,
                organization=organization,
                actor=actor,
                resource=locked,
                metadata={**meta, "template_id": str(locked.template_id)},
                request=request,
            )
    return locked


def delete_campaign(campaign: Campaign, *, actor: Any, request: Any = None) -> None:
    with transaction.atomic():
        locked = Campaign.objects.select_for_update().get(pk=campaign.pk)
        if not locked.is_editable:
            raise CampaignStateError("Only draft campaigns can be deleted; cancel it instead.")
        audit(
            actions.CAMPAIGN_DELETED,
            organization=locked.business.organization,
            actor=actor,
            resource=locked,
            metadata={"business_id": str(locked.business_id), "name": locked.name},
            request=request,
        )
        locked.delete()


# ---------------------------------------------------------------------------
# Saved audiences
# ---------------------------------------------------------------------------


def save_audience(
    *,
    business: Business,
    actor: Any,
    data: dict[str, Any],
    audience: Audience | None = None,
    request: Any = None,
) -> Audience:
    name = str(data.get("name", audience.name if audience else "") or "").strip()
    if not 1 <= len(name) <= 120:
        raise CampaignValidationError(details={"name": "1-120 characters required"})
    definition = audience_mod.validate_definition(
        business, data.get("definition", audience.definition if audience else None)
    )
    created = audience is None
    target = audience or Audience(
        organization_id=business.organization_id,
        business=business,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    target.name = name
    target.description = str(data.get("description", target.description) or "")[:500]
    target.definition = definition
    try:
        with transaction.atomic():
            target.save()
            audit(
                actions.AUDIENCE_CREATED if created else actions.AUDIENCE_UPDATED,
                organization=business.organization,
                actor=actor,
                resource=target,
                metadata={"business_id": str(business.pk), "definition": definition},
                request=request,
            )
    except IntegrityError as exc:
        raise ConflictError("An audience with this name already exists.") from exc
    return target


def delete_audience(audience: Audience, *, actor: Any, request: Any = None) -> None:
    with transaction.atomic():
        audit(
            actions.AUDIENCE_DELETED,
            organization=audience.business.organization,
            actor=actor,
            resource=audience,
            metadata={"business_id": str(audience.business_id), "name": audience.name},
            request=request,
        )
        audience.delete()
