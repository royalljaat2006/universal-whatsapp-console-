"""Pre-launch validation and the launch summary.

Each check is ``{"code", "ok", "severity": "error"|"warning", "message"}``.
Any failed *error* check blocks launch (and a scheduled campaign is re-checked
when it starts, because a template or connection can change meanwhile).
Warnings are shown in the launch summary for an informed decision.
"""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.campaigns import audience as audience_mod
from apps.campaigns import variables
from apps.campaigns.models import Campaign
from apps.contacts.models import Contact
from apps.templates.parameters import UnsupportedTemplateError, describe
from apps.whatsapp.models import ConnectionState, WhatsAppBusinessAccount, WhatsAppPhoneNumber
from apps.whatsapp.services import sender_ref
from core.exceptions import ConfigurationRequiredError, DomainError

MAX_SCHEDULE_AHEAD = timedelta(days=90)
_TIER = re.compile(r"^TIER_(\d+)(K?)$")


@dataclass
class LaunchReport:
    checks: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def add(self, code: str, ok: bool, message: str, severity: str = "error") -> None:
        self.checks.append({"code": code, "ok": ok, "severity": severity, "message": message})

    @property
    def launchable(self) -> bool:
        return all(c["ok"] for c in self.checks if c["severity"] == "error")

    def as_dict(self) -> dict[str, Any]:
        return {"launchable": self.launchable, "checks": self.checks, "summary": self.summary}


class _SkipAudience(Exception):
    pass


class CampaignNotLaunchableError(DomainError):
    code = "CAMPAIGN_NOT_LAUNCHABLE"
    status_code = 422
    default_message = "The campaign cannot be launched; see the failed checks."


def tier_limit(tier: str) -> int | None:
    """Unique-recipient limit per 24h for a Meta messaging tier (TIER_1K …)."""
    match = _TIER.match(tier or "")
    if not match:
        return None
    return int(match.group(1)) * (1000 if match.group(2) else 1)


def evaluate(campaign: Campaign, *, for_start: bool = False, audience: bool = True) -> LaunchReport:
    """``audience=False`` skips audience checks (resume/retry of a campaign
    whose snapshot already exists)."""
    report = LaunchReport()
    number: WhatsAppPhoneNumber = campaign.phone_number
    template = campaign.template
    business = campaign.business

    # Ownership (defence in depth: the API also checks these on write).
    same_business = number.business_id == business.pk and template.business_id == business.pk
    report.add(
        "OWNERSHIP",
        same_business,
        (
            "Number and template belong to this business."
            if same_business
            else "The number or template belongs to a different business."
        ),
    )

    account: WhatsAppBusinessAccount = number.business_account
    connected = number.status == WhatsAppPhoneNumber.Status.CONNECTED
    report.add(
        "NUMBER_CONNECTED",
        connected,
        (
            f"Number {number.display_phone_number} is connected."
            if connected
            else f"Number {number.display_phone_number} is '{number.status}', not connected."
        ),
    )
    waba_ok = account.connection_state == ConnectionState.CONNECTED
    report.add(
        "WABA_CONNECTED",
        waba_ok,
        (
            "WhatsApp Business Account connection verified."
            if waba_ok
            else f"WhatsApp Business Account connection is '{account.connection_state}'."
        ),
    )
    try:
        sender_ref(number)
        report.add("PROVIDER_CREDENTIALS", True, "Provider credentials are configured.")
    except (ConfigurationRequiredError, PermissionError, AttributeError) as exc:
        report.add("PROVIDER_CREDENTIALS", False, f"Provider credentials unavailable: {exc}")

    template_ok = (
        template.business_account_id == number.business_account_id
        and template.is_sendable
        and not template.missing_from_provider
    )
    report.add(
        "TEMPLATE_APPROVED",
        template_ok,
        (
            f"Template '{template.name}' ({template.language}) is approved."
            if template_ok
            else f"Template '{template.name}' is '{template.status}' or not on this number's WABA."
        ),
    )
    try:
        describe(template)
        structure_ok = True
    except UnsupportedTemplateError as exc:
        structure_ok = False
        report.add("TEMPLATE_SUPPORTED", False, str(exc))
    if structure_ok:
        report.add("TEMPLATE_SUPPORTED", True, "Template structure is supported.")

    mapping: dict[str, Any] | None = None
    if structure_ok:
        try:
            mapping = variables.validate_mapping(template, business, campaign.variable_mapping)
            report.add("VARIABLES_MAPPED", True, "Every template variable is mapped.")
        except variables.VariableMappingError as exc:
            report.add(
                "VARIABLES_MAPPED",
                False,
                (
                    "Template variables are not fully mapped: "
                    + "; ".join(f"{k}: {v}" for k, v in (exc.details.get("fields") or {}).items())
                    if exc.details.get("fields")
                    else exc.message
                ),
            )

    count = 0
    try:
        if not audience:
            count = campaign.recipients.count()
            raise _SkipAudience
        definition = audience_mod.validate_definition(business, campaign.audience_definition)
        qs = audience_mod.audience_queryset(business, definition)
        count = qs.count()
        report.summary["audience_conditions"] = audience_mod.describe(definition)
        report.summary["audience_match"] = definition["match"]
        limit = settings.CAMPAIGN_MAX_RECIPIENTS
        report.add(
            "AUDIENCE_NOT_EMPTY",
            count > 0,
            (
                f"{count} recipient(s) match the audience."
                if count
                else "No contact matches the audience."
            ),
        )
        report.add(
            "AUDIENCE_SIZE",
            count <= limit,
            (
                f"Audience within the {limit} recipient limit."
                if count <= limit
                else f"Audience of {count} exceeds the {limit} recipient limit."
            ),
        )
        if mapping is not None:
            missing_q = variables.missing_filter(template, mapping)
            missing = qs.filter(missing_q).count() if missing_q is not None else 0
            report.summary["recipients_missing_variables"] = missing
            report.add(
                "VARIABLE_VALUES",
                missing == 0,
                (
                    "Every recipient has values for all variables."
                    if missing == 0
                    else f"{missing} recipient(s) lack a variable value and will be skipped."
                ),
                severity="warning",
            )
        not_opted_in = qs.exclude(opt_in_status=Contact.OptInStatus.OPTED_IN).count()
        report.summary["recipients_without_recorded_opt_in"] = not_opted_in
        report.add(
            "OPT_IN",
            not_opted_in == 0,
            (
                "All recipients have a recorded opt-in."
                if not not_opted_in
                else f"{not_opted_in} recipient(s) have no recorded opt-in. WhatsApp requires "
                "opt-in for business-initiated messages."
            ),
            severity="warning",
        )
        limit_24h = tier_limit(number.messaging_limit_tier)
        report.summary["messaging_limit_tier"] = number.messaging_limit_tier or None
        if limit_24h is not None:
            report.add(
                "MESSAGING_LIMIT",
                count <= limit_24h,
                (
                    f"Audience fits the number's messaging limit ({number.messaging_limit_tier})."
                    if count <= limit_24h
                    else f"Audience ({count}) exceeds the portfolio messaging limit "
                    f"{number.messaging_limit_tier} per 24h; WhatsApp will reject the excess."
                ),
                severity="warning",
            )
    except _SkipAudience:
        pass
    except audience_mod.AudienceDefinitionError as exc:
        report.add(
            "AUDIENCE_VALID", False, f"Audience definition invalid: {exc.details or exc.message}"
        )

    rate = campaign.max_messages_per_minute
    max_rate = min(settings.CAMPAIGN_MAX_RATE_PER_MINUTE, int(number.effective_throughput_mps * 60))
    rate_ok = 1 <= rate <= max_rate
    report.add(
        "RATE_LIMIT",
        rate_ok,
        (
            f"Send rate {rate}/min."
            if rate_ok
            else f"Send rate must be between 1 and {max_rate} messages per minute."
        ),
    )

    if not for_start and campaign.scheduled_at is not None:
        now = timezone.now()
        in_future = campaign.scheduled_at > now
        report.add(
            "SCHEDULE",
            in_future and campaign.scheduled_at - now <= MAX_SCHEDULE_AHEAD,
            (
                f"Scheduled for {campaign.scheduled_at.isoformat()}."
                if in_future
                else "The scheduled time is in the past."
            ),
        )

    # Templates may be sent outside the 24h window; that is the only kind a
    # campaign sends, so the window policy never blocks campaign messages.
    report.add(
        "WINDOW_POLICY",
        True,
        "Campaigns send approved templates, which WhatsApp allows outside the 24h window.",
    )

    minutes = (count / rate) if rate_ok and rate else None
    report.summary.update(
        {
            "business": business.name,
            "phone_number": number.display_phone_number,
            "template": f"{template.name} ({template.language}, {template.category})",
            "recipients": count,
            "rate_per_minute": rate,
            "estimated_duration_minutes": round(minutes, 1) if minutes is not None else None,
            "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
            "timezone": campaign.timezone or None,
        }
    )
    return report
