"""Plain-language status and recovery guidance for a template.

``state`` separates what the console knows locally from what Meta reported:

- ``draft``      only in the Template Studio, Meta has not seen it
- ``submitted``  the console sent it; Meta has not reported any status yet
- ``pending``    Meta says it is in review
- ``approved`` / ``rejected`` / ``paused`` / ``disabled``  Meta's own verdict
- ``unknown``    a status value this console does not recognise (shown verbatim)

``verified`` says whether the status was *reported by Meta after we
submitted* (a sync or a status webhook) rather than merely returned in the
response to our own create request. A successful HTTP response never counts
as approval: only Meta's reported ``APPROVED`` does, and only a later sync or
webhook confirms it.

Rejection explanations are tied to Meta's published review guidance
(template-review page, read 2026-10-12). Meta's reason code is always shown
verbatim; the plain text next to it is ours and is labelled as such.
"""

from typing import Any

from apps.templates.models import MessageTemplate, TemplateDraft

WHATSAPP_MANAGER_URL = "https://business.facebook.com/latest/whatsapp_manager"
TEMPLATE_REVIEW_URL = (
    "https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/"
    "template-review"
)

_STATE_BY_STATUS = {
    "APPROVED": "approved",
    "PENDING": "pending",
    "PENDING_REVIEW": "pending",
    "IN_REVIEW": "pending",
    "REJECTED": "rejected",
    "PAUSED": "paused",
    "DISABLED": "disabled",
}

_REASONS: dict[str, tuple[str, str]] = {
    "INVALID_FORMAT": (
        "Meta says the template is formatted wrongly. Common causes: a variable missing, "
        "brackets that do not match, variables out of order, special characters inside a "
        "variable, or a variable at the very start or end.",
        "Check every {{variable}}, number them 1, 2, 3 in order, and keep a few words before the "
        "first and after the last.",
    ),
    "ABUSIVE_CONTENT": (
        "Meta says the wording is abusive or threatening, for example threatening legal action "
        "or public shaming.",
        "Remove the threatening wording and keep the message neutral and factual.",
    ),
    "SCAM": (
        "Meta says the message looks like spam or a scam.",
        "Remove anything that looks like spam, and make clear who you are and why you are writing.",
    ),
    "TAG_CONTENT_MISMATCH": (
        "Meta says the content does not fit the category you chose (from the code's name: the "
        "category and the wording disagree).",
        "Pick the category that matches the message: an update for something the customer did is "
        "utility; a promotion or offer is marketing.",
    ),
}

_GENERAL_STEPS = (
    "Common reasons from Meta's guidance: variables written wrongly, wording that breaks "
    "WhatsApp's commerce or business policy, abusive or threatening text, a body that is too "
    "long, the same wording as another template, or spam-like content."
)

_EXPLANATION: dict[str, tuple[str, str]] = {
    "draft": (
        "This is a draft. Meta has not seen it, and it cannot be used to send messages.",
        "Finish it, check it, and submit it for review when you are ready.",
    ),
    "submitted": (
        "The console sent this to Meta, but Meta has not reported a status yet.",
        'Check again soon with "Check with Meta". Do not submit it again.',
    ),
    "pending": (
        "Meta is reviewing this template. Reviews usually finish within 24 hours.",
        "Wait for Meta's decision. You cannot send it until it is approved.",
    ),
    "approved": (
        "Meta approved this template. It can be used to send messages.",
        "",
    ),
    "rejected": (
        "Meta rejected this template, so it cannot be sent.",
        "Fix the reason below and send a corrected copy, or edit and appeal it in WhatsApp Manager.",
    ),
    "paused": (
        "Meta paused this template, usually because of low quality or negative feedback, so it "
        "cannot be sent right now.",
        "Open WhatsApp Manager to see why and how to restore it.",
    ),
    "disabled": (
        "Meta disabled this template, so it cannot be sent.",
        "Edit and resubmit it in WhatsApp Manager, or create a corrected copy here.",
    ),
    "unknown": (
        "Meta reported a status this console does not recognise, so it is shown exactly as "
        "Meta wrote it. The template is not treated as sendable.",
        "Open WhatsApp Manager to see what the status means.",
    ),
}


def state_of(template: MessageTemplate | None, draft: TemplateDraft | None = None) -> str:
    if template is None:
        if draft is not None and draft.status in (
            TemplateDraft.Status.SUBMITTED,
            TemplateDraft.Status.UNCONFIRMED,
            TemplateDraft.Status.SUBMITTING,
        ):
            return "submitted"
        return "draft"
    status = (template.status or "").upper()
    if not status:
        return "submitted"
    return _STATE_BY_STATUS.get(status, "unknown")


def meta_confirmed(template: MessageTemplate, draft: TemplateDraft | None) -> bool:
    """Was this status reported by Meta (sync / webhook) after we submitted?

    For templates that were never submitted from the console (they came from
    Meta's own list) the status is Meta's by origin."""
    if draft is None or draft.submitted_at is None:
        return template.last_synced_at is not None or template.last_status_event_at is not None
    return bool(
        (template.last_status_event_at and template.last_status_event_at > draft.submitted_at)
        or (template.last_synced_at and template.last_synced_at > draft.submitted_at)
    )


def explain(template: MessageTemplate | None, draft: TemplateDraft | None = None) -> dict[str, Any]:
    state = state_of(template, draft)
    explanation, next_step = _EXPLANATION[state]
    if draft is not None and draft.status == TemplateDraft.Status.UNCONFIRMED:
        explanation = (
            "The console is not sure Meta received this template: the request did not finish."
        )
        next_step = 'Use "Check with Meta". It cannot be submitted again until that is settled.'
    reason: dict[str, str] | None = None
    if template is not None and state == "rejected":
        code = (template.rejected_reason or "").strip()
        plain, fix = _REASONS.get(code.upper(), ("", ""))
        reason = {
            "code": code,
            "meaning": plain
            or (
                "Meta did not give a reason this console recognises."
                if code and code.upper() != "NONE"
                else "Meta did not give a specific reason."
            ),
            "how_to_fix": fix or _GENERAL_STEPS,
        }
    verified = False
    if template is not None:
        verified = meta_confirmed(template, draft)
    can_send = bool(
        template is not None
        and state == "approved"
        and not template.missing_from_provider
        and verified
    )
    return {
        "state": state,
        "meta_status": template.status if template else "",
        "verified_by_meta": verified,
        "explanation": explanation,
        "next_step": next_step,
        "reason": reason,
        "can_send": can_send,
        "can_fix_as_new": state in {"rejected", "disabled", "paused"},
        "links": (
            [
                {"label": "Open WhatsApp Manager (Meta)", "url": WHATSAPP_MANAGER_URL},
                {"label": "Meta's template review guidance", "url": TEMPLATE_REVIEW_URL},
            ]
            if state in {"rejected", "paused", "disabled", "unknown", "pending"}
            else []
        ),
        "review_note": (
            "Meta usually decides within 24 hours. This status is only as fresh as the last "
            "sync or update from Meta."
            if state in {"pending", "submitted"}
            else ""
        ),
    }
