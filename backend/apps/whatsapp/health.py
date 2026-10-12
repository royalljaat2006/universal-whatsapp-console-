"""Plain-language health assessment of a business's WhatsApp connections.

Every check is derived from a signal this system actually stores: values Meta
reported when the account/number was last verified or synced (connection,
review, verification, number status, quality rating, name review, messaging
limit, webhook subscription, account alerts) and the outcome of the messages
this console sent. Nothing is estimated or defaulted to "fine":

* a value that is missing is reported as ``unknown`` (never ``ok``);
* a provider value this module does not recognise is shown **verbatim** as a
  warning, because Meta's vocabularies change - the app must not guess;
* each check says whether its signal came from the ``provider`` (Meta) or from
  a ``console`` rule (our own threshold), so the two are never confused;
* each check says where it can be fixed: ``console`` (an action in this app),
  ``meta`` (only in Meta's own interface, e.g. WhatsApp Manager) or ``none``.

The functions here are pure (they take model instances and numbers and return
dataclasses) so they can be tested without a database round trip.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from apps.messaging.services import failure_categories as fc
from apps.whatsapp.models import (
    ConnectionState,
    SyncState,
    WhatsAppBusinessAccount,
    WhatsAppPhoneNumber,
)

State = Literal["ok", "warning", "problem", "unknown"]
FixIn = Literal["console", "meta", "none"]
Basis = Literal["provider", "console"]
Overall = Literal["healthy", "attention", "problem", "unknown"]

# Console rules (our thresholds, not Meta's): documented here so they are visible.
STALE_AFTER = timedelta(hours=24)
RECENT_WINDOW = timedelta(hours=24)
MIN_SENDS_FOR_RATE = 10
FAILURE_WARN_RATE = 0.2
FAILURE_PROBLEM_RATE = 0.5

_TIER = re.compile(r"^TIER_(\d+)(K?)$")


@dataclass(frozen=True)
class Check:
    code: str
    state: State
    title: str
    detail: str
    next_step: str = ""
    fix_in: FixIn = "none"
    basis: Basis = "provider"
    # Console action that can help right now ("refresh" re-reads Meta).
    action: str = ""


@dataclass
class Assessment:
    subject: str  # "account" | "number"
    checks: list[Check] = field(default_factory=list)

    @property
    def overall(self) -> Overall:
        return overall_of(self.checks)


def overall_of(checks: list[Check]) -> Overall:
    states = {c.state for c in checks}
    if "problem" in states:
        return "problem"
    if "warning" in states:
        return "attention"
    if "ok" in states:
        return "healthy"
    return "unknown"


def _trim(text: str, limit: int = 240) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _age(now: datetime, then: datetime | None) -> str:
    if then is None:
        return "never"
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 86400:
        return f"{seconds // 3600} hours ago"
    return f"{seconds // 86400} days ago"


def _freshness(now: datetime, observed: datetime | None, what: str) -> Check:
    if observed is None:
        return Check(
            "freshness",
            "unknown",
            "Status not checked yet",
            f"{what} has never been read from Meta, so nothing below can be confirmed.",
            "Use “Refresh from Meta”.",
            "console",
            "console",
            "refresh",
        )
    if now - observed > STALE_AFTER:
        return Check(
            "freshness",
            "warning",
            "Status may be out of date",
            f"Last checked with Meta {_age(now, observed)}. Things may have changed since.",
            "Use “Refresh from Meta” to read the current status.",
            "console",
            "console",
            "refresh",
        )
    return Check(
        "freshness",
        "ok",
        "Status is recent",
        f"Last checked with Meta {_age(now, observed)}.",
        basis="console",
    )


# --------------------------------------------------------------------------- account


def assess_account(waba: WhatsAppBusinessAccount, *, now: datetime) -> Assessment:
    out = Assessment("account")
    add = out.checks.append

    state = waba.connection_state
    if state == ConnectionState.CONNECTED:
        add(Check("connection", "ok", "Connected to WhatsApp", "Meta accepted the connection."))
    elif state == ConnectionState.PENDING:
        add(
            Check(
                "connection",
                "warning",
                "Still connecting",
                "The connection has not been confirmed with Meta yet.",
                "Use “Refresh from Meta”. If it stays pending, ask your administrator to check "
                "the access token and the WhatsApp Business Account ID.",
                "console",
                action="refresh",
            )
        )
    elif state == ConnectionState.ERROR:
        reason = _trim(waba.connection_error) or "Meta did not accept the connection."
        add(
            Check(
                "connection",
                "problem",
                "Connection problem",
                f"Meta reported: {reason}",
                "Ask your administrator to check that the access token is still valid and has "
                "WhatsApp permissions, then use “Refresh from Meta”.",
                "console",
                action="refresh",
            )
        )
    else:
        add(
            Check(
                "connection",
                "problem",
                "Disconnected",
                "This WhatsApp Business Account is marked as disconnected.",
                "Reconnect it from the WhatsApp page, or ask your administrator.",
                "console",
            )
        )

    if waba.status == WhatsAppBusinessAccount.Status.RESTRICTED:
        add(
            Check(
                "restricted",
                "problem",
                "Account restricted",
                "Meta has restricted this WhatsApp Business Account.",
                "Review the restriction notice in WhatsApp Manager (Meta Business Suite) and "
                "follow Meta's instructions. This cannot be lifted from this console.",
                "meta",
            )
        )

    if waba.webhooks_subscribed_at:
        add(
            Check(
                "webhooks",
                "ok",
                "Receiving WhatsApp events",
                "New replies and delivery updates are being delivered to this app.",
            )
        )
    elif state == ConnectionState.CONNECTED:
        add(
            Check(
                "webhooks",
                "problem",
                "Not receiving WhatsApp events",
                "This app is not subscribed to this account's events, so new customer replies "
                "and delivery updates may not appear.",
                "Use “Refresh from Meta” to re-check the subscription, or ask your administrator "
                "to subscribe the app.",
                "console",
                action="refresh",
            )
        )

    review = (waba.account_review_status or "").strip()
    if not review:
        add(
            Check(
                "account_review",
                "unknown",
                "Account review",
                "Meta has not reported a review status for this account.",
                fix_in="none",
            )
        )
    elif review.upper() == "APPROVED":
        add(Check("account_review", "ok", "Account review approved", "Meta approved this account."))
    elif review.upper() == "REJECTED":
        add(
            Check(
                "account_review",
                "problem",
                "Account review rejected",
                "Meta rejected this WhatsApp Business Account.",
                "Open WhatsApp Manager (Meta Business Suite) to see the reason and appeal.",
                "meta",
            )
        )
    else:
        add(
            Check(
                "account_review",
                "warning",
                "Account review in progress or needs attention",
                f"Meta reports the review status as “{review}”.",
                "Check WhatsApp Manager (Meta Business Suite) for details.",
                "meta",
            )
        )

    verification = (waba.business_verification_status or "").strip()
    if not verification:
        add(
            Check(
                "business_verification",
                "unknown",
                "Business verification",
                "Meta has not reported a verification status for this business.",
            )
        )
    elif verification.lower() == "verified":
        add(
            Check(
                "business_verification", "ok", "Business verified", "Meta verified your business."
            )
        )
    else:
        add(
            Check(
                "business_verification",
                "warning",
                "Business not verified",
                f"Meta reports the verification status as “{verification}”. Unverified "
                "businesses can face lower limits.",
                "Complete business verification in Meta Business Suite.",
                "meta",
            )
        )

    template_sync = waba.template_sync_state
    if template_sync == SyncState.FAILED:
        add(
            Check(
                "templates_sync",
                "warning",
                "Templates may be out of date",
                f"The last template sync failed: {_trim(waba.template_sync_error) or 'no reason given'}.",
                "Use “Sync from Meta” on the Templates page.",
                "console",
            )
        )
    elif template_sync == SyncState.NEVER:
        add(
            Check(
                "templates_sync",
                "unknown",
                "Templates not synced yet",
                "Your templates have not been read from Meta yet.",
                "Use “Sync from Meta” on the Templates page.",
                "console",
            )
        )
    elif template_sync == SyncState.SUCCEEDED:
        add(
            Check(
                "templates_sync",
                "ok",
                "Templates synced",
                f"Templates were last synced {_age(now, waba.templates_last_synced_at)}.",
            )
        )

    add(_freshness(now, waba.last_verified_at, "This account"))
    return out


# --------------------------------------------------------------------------- number

_FAILURE_GUIDANCE: dict[str, tuple[str, str, FixIn]] = {
    fc.INVALID_RECIPIENT: (
        "Some recipients are not valid WhatsApp numbers or cannot receive this message.",
        "Check the contact's number and consent. Retrying will not help.",
        "console",
    ),
    fc.PROVIDER_REJECTION: (
        "WhatsApp rejected some messages.",
        "Open the failed message to see Meta's reason.",
        "console",
    ),
    fc.RATE_LIMIT: (
        "Messages were sent faster than WhatsApp allows.",
        "Slow the sending speed; messages that failed this way are safe to retry later.",
        "console",
    ),
    fc.AUTHENTICATION: (
        "The connection to WhatsApp was not authorised (access token or permissions).",
        "Ask your administrator to check the access token, then retry.",
        "console",
    ),
    fc.TEMPLATE_ERROR: (
        "A template problem (not approved, wrong variables or language) stopped messages.",
        "Check the template's status on the Templates page.",
        "console",
    ),
    fc.POLICY_WINDOW: (
        "The customer's 24-hour reply window was closed, so only an approved template can be sent.",
        "Send an approved template instead of free text.",
        "console",
    ),
    fc.TIMEOUT: (
        "WhatsApp did not confirm some messages; the outcome is unknown.",
        "These are never retried automatically (they could deliver twice). Check the "
        "conversation before sending again.",
        "console",
    ),
    fc.TEMPORARY: (
        "A temporary problem on WhatsApp's side.",
        "Safe to retry later.",
        "console",
    ),
}


def failure_guidance(category: str) -> tuple[str, str, FixIn]:
    return _FAILURE_GUIDANCE.get(
        category,
        (
            "Some messages failed for a reason this console does not recognise.",
            "Open a failed message to see exactly what Meta reported.",
            "console",
        ),
    )


@dataclass(frozen=True)
class RecentDelivery:
    """Outbound messages from this number in the recent window."""

    total: int = 0
    failed: int = 0
    #: ``(category, count)`` for failed messages, most frequent first.
    causes: tuple[tuple[str, int], ...] = ()


def describe_limit(tier: str) -> tuple[State, str]:
    """(state, text) for a ``messaging_limit_tier`` value."""
    tier = (tier or "").strip()
    if not tier:
        return "unknown", "Meta has not reported a messaging limit for this number."
    if tier.upper() == "TIER_UNLIMITED":
        return "ok", "Meta reports no fixed limit on how many customers you can start chats with."
    match = _TIER.match(tier.upper())
    if match:
        amount = f"{match.group(1)}{'K' if match.group(2) else ''}"
        return (
            "ok",
            f"You can start conversations with up to {amount} different customers in 24 hours. "
            "Meta shares this limit across your whole business portfolio.",
        )
    return "unknown", f"Meta reports the limit as “{tier}”, which this console does not recognise."


def assess_number(
    phone: WhatsAppPhoneNumber,
    *,
    now: datetime,
    recent: RecentDelivery | None = None,
) -> Assessment:
    out = Assessment("number")
    add = out.checks.append
    seen_by_meta = f" (Meta reports: {phone.provider_status})" if phone.provider_status else ""

    status = phone.status
    if status == WhatsAppPhoneNumber.Status.CONNECTED:
        add(
            Check(
                "status",
                "ok",
                "Ready to send and receive",
                f"This number is connected{seen_by_meta}.",
            )
        )
    elif status == WhatsAppPhoneNumber.Status.PENDING:
        add(
            Check(
                "status",
                "warning",
                "Not ready yet",
                f"Registration is not finished{seen_by_meta}. It cannot send or receive yet.",
                "Finish setting up the number in WhatsApp Manager, then use “Refresh from Meta”.",
                "meta",
                action="refresh",
            )
        )
    elif status == WhatsAppPhoneNumber.Status.DISCONNECTED:
        add(
            Check(
                "status",
                "problem",
                "Number disconnected",
                f"This number is disconnected{seen_by_meta}.",
                "Reconnect or re-register the number in WhatsApp Manager.",
                "meta",
            )
        )
    else:
        add(
            Check(
                "status",
                "problem",
                "Number "
                + ("flagged" if status == WhatsAppPhoneNumber.Status.FLAGGED else "restricted"),
                f"Meta has {status} this number{seen_by_meta}.",
                "Review the notice in WhatsApp Manager (Meta Business Suite). This cannot be "
                "resolved from this console.",
                "meta",
            )
        )

    quality = (phone.quality_rating or "").strip().upper()
    explain = (
        "Quality reflects how customers react to your messages (for example blocks and reports)."
    )
    if quality == "GREEN":
        add(Check("quality", "ok", "Quality is high", explain))
    elif quality == "YELLOW":
        add(
            Check(
                "quality",
                "warning",
                "Quality is medium",
                f"{explain} Medium means some customers are reacting badly.",
                "Message only people who asked to hear from you, and review the templates you "
                "send most. Manage this in WhatsApp Manager.",
                "meta",
            )
        )
    elif quality == "RED":
        add(
            Check(
                "quality",
                "problem",
                "Quality is low",
                f"{explain} Low quality can lead to a reduced limit or restrictions.",
                "Pause marketing sends, review who you message and which templates you use, "
                "and follow the guidance in WhatsApp Manager.",
                "meta",
            )
        )
    elif not quality or quality == "UNKNOWN":
        add(
            Check(
                "quality",
                "unknown",
                "Quality not rated yet",
                "Meta has not given this number a quality rating yet (common for new numbers "
                "or numbers with little recent activity).",
            )
        )
    else:
        add(
            Check(
                "quality",
                "warning",
                "Quality rating",
                f"Meta reports the quality rating as “{phone.quality_rating}”.",
                "Check WhatsApp Manager for details.",
                "meta",
            )
        )

    name = (phone.name_status or "").strip()
    if not name or name.upper() == "NONE":
        add(
            Check(
                "display_name",
                "unknown",
                "Display name",
                "Meta has not reported a name review status.",
            )
        )
    elif name.upper() in {"APPROVED", "AVAILABLE_WITHOUT_REVIEW"}:
        add(
            Check(
                "display_name",
                "ok",
                "Display name approved",
                "Customers see your approved business name.",
            )
        )
    elif name.upper() == "PENDING_REVIEW":
        add(
            Check(
                "display_name",
                "warning",
                "Display name under review",
                "Meta is reviewing your business display name.",
                "No action needed; Meta decides. Check WhatsApp Manager for the result.",
                "meta",
            )
        )
    else:
        add(
            Check(
                "display_name",
                "warning",
                "Display name needs attention",
                f"Meta reports the name review status as “{name}”.",
                "Open WhatsApp Manager to see why and what to change.",
                "meta",
            )
        )

    limit_state, limit_text = describe_limit(phone.messaging_limit_tier)
    add(
        Check(
            "messaging_limit",
            limit_state,
            "Messaging limit" if limit_state != "ok" else "Your messaging limit",
            limit_text,
            fix_in="none",
        )
    )

    verification = (phone.code_verification_status or "").strip()
    if verification and verification.upper() != "VERIFIED":
        add(
            Check(
                "number_verification",
                "warning",
                "Number not verified",
                f"Meta reports the number's verification status as “{verification}”.",
                "Complete number verification in WhatsApp Manager.",
                "meta",
            )
        )

    if recent is not None:
        add(_delivery_check(recent))

    if phone.last_error:
        add(
            Check(
                "last_error",
                "warning",
                "Last refresh reported a problem",
                _trim(phone.last_error),
                "Use “Refresh from Meta”.",
                "console",
                "console",
                "refresh",
            )
        )

    add(_freshness(now, phone.last_synced_at, "This number"))
    return out


def _delivery_check(recent: RecentDelivery) -> Check:
    if recent.total == 0:
        return Check(
            "recent_delivery",
            "unknown",
            "Recent sending",
            "No messages were sent from this number in the last 24 hours, so there is nothing to measure.",
            basis="console",
        )
    if recent.failed == 0:
        return Check(
            "recent_delivery",
            "ok",
            "Recent messages went through",
            f"{recent.total} message(s) sent in the last 24 hours, none failed.",
            basis="console",
        )
    rate = recent.failed / recent.total
    top = recent.causes[0][0] if recent.causes else fc.UNKNOWN
    why, next_step, fix = failure_guidance(top)
    summary = (
        f"{recent.failed} of {recent.total} messages failed in the last 24 hours. "
        f"Most common cause: {fc.LABELS[top].lower()}. {why}"
    )
    if recent.total >= MIN_SENDS_FOR_RATE and rate >= FAILURE_PROBLEM_RATE:
        state: State = "problem"
    elif (recent.total >= MIN_SENDS_FOR_RATE and rate >= FAILURE_WARN_RATE) or recent.failed >= 3:
        state = "warning"
    else:
        state = "ok"
    return Check(
        "recent_delivery", state, "Recent message failures", summary, next_step, fix, "console"
    )


def summarize(assessments: list[Assessment]) -> tuple[Overall, str]:
    """Overall state and one plain sentence for the whole business."""
    all_checks = [c for a in assessments for c in a.checks]
    overall = overall_of(all_checks)
    problems = sum(1 for c in all_checks if c.state == "problem")
    warnings = sum(1 for c in all_checks if c.state == "warning")
    if not assessments:
        return "unknown", "No WhatsApp account or number is connected yet."
    if overall == "healthy":
        return overall, "Everything Meta has reported looks healthy."
    if overall == "problem":
        return overall, f"{problems} thing{'s' if problems != 1 else ''} need fixing" + (
            f" and {warnings} need attention." if warnings else "."
        )
    if overall == "attention":
        return overall, f"{warnings} thing{'s' if warnings != 1 else ''} need attention."
    return overall, "There is not enough information from Meta yet to say."


def as_dict(check: Check) -> dict[str, Any]:
    return {
        "code": check.code,
        "state": check.state,
        "title": check.title,
        "detail": check.detail,
        "next_step": check.next_step,
        "fix_in": check.fix_in,
        "basis": check.basis,
        "action": check.action,
    }
