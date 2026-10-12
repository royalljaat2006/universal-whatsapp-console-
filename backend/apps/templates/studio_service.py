"""Template Studio: drafts, the confirmed submit, and reconciliation.

Safety rules enforced here (server-side, whatever the browser does):

- A draft is local. ``submit_draft`` is the only code path that sends anything
  to Meta, and only with ``confirm=True`` and a ``content_hash`` that equals
  the hash of the draft *as it is now* (so what the person reviewed is exactly
  what is sent, and a stale tab cannot submit older or newer content).
- Duplicate submission is impossible by construction: the draft row is locked
  and moved ``draft -> submitting`` in its own committed transaction BEFORE
  the provider call (provider calls never run inside a database transaction);
  a concurrent or repeated submit sees ``submitting`` / ``submitted`` and is
  refused. A unique constraint on (account, name, language) backs this up.
- If the outcome is unknown (timeout, crash) the draft becomes ``unconfirmed``
  and cannot be re-submitted until a later sync shows whether Meta has it.
- A successful create response is stored as what Meta *said*; approval is
  never assumed. ``guidance.meta_confirmed`` tells whether Meta has confirmed
  the status after the submit (sync / status webhook).
- Audit entries carry ids, name, language, category, hash prefix and error
  codes: never the message text, examples or credentials.
"""

import logging
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.templates import studio
from apps.templates.models import MessageTemplate, TemplateDraft
from apps.templates.parameters import UnsupportedTemplateError, describe
from apps.templates.services import upsert_template
from apps.whatsapp.models import WhatsAppBusinessAccount
from apps.whatsapp.services import account_ref, provider_for
from core.exceptions import ConfigurationRequiredError, ConflictError, DomainError
from core.secrets import SecretReferenceError
from providers.whatsapp.errors import ProviderError, Retryability
from providers.whatsapp.types import TemplateSpec

logger = logging.getLogger(__name__)

# A submit that has been "submitting" this long did not finish normally.
SUBMITTING_STALE_AFTER = timedelta(minutes=5)


class TemplateInvalidError(DomainError):
    code = "TEMPLATE_INVALID"
    status_code = 422
    default_message = "The template has problems that must be fixed before it can be sent to Meta."


class DraftConflictError(ConflictError):
    code = "DRAFT_CONFLICT"


class SubmitFailedError(DomainError):
    code = "TEMPLATE_SUBMIT_FAILED"
    status_code = 502
    default_message = "Meta did not accept the template."


# --------------------------------------------------------------------------- helpers


def _body_and_footer(components: list[dict[str, Any]]) -> tuple[str, str]:
    body = footer = ""
    for component in components or []:
        kind = str(component.get("type", "")).upper()
        if kind == "BODY":
            body = str(component.get("text") or "").strip()
        elif kind == "FOOTER":
            footer = str(component.get("text") or "").strip()
    return body, footer


def existing_templates(account: WhatsAppBusinessAccount) -> list[dict[str, str]]:
    rows = []
    for t in MessageTemplate.objects.filter(business_account=account, missing_from_provider=False):
        body, footer = _body_and_footer(t.components)
        rows.append({"name": t.name, "language": t.language, "body": body, "footer": footer})
    return rows


def run_checks(
    account: WhatsAppBusinessAccount,
    *,
    name: str,
    language: str,
    category: str,
    parameter_format: str,
    content: dict[str, Any] | None,
) -> studio.Validation:
    existing = existing_templates(account)
    verified = (account.business_verification_status or "").lower()
    return studio.validate(
        name=name,
        language=language,
        category=category,
        parameter_format=parameter_format,
        content=content,
        existing=existing,
        template_count=len(existing),
        business_verified=True if verified == "verified" else (False if verified else None),
    )


def draft_hash(draft: TemplateDraft, validation: studio.Validation) -> str:
    return studio.hash_for(
        draft.name, draft.language, draft.category, draft.parameter_format, validation
    )


def _audit(
    action: str, draft: TemplateDraft, actor: Any, request: Any = None, **extra: Any
) -> None:
    audit(
        action,
        organization=draft.business_account.organization,
        actor=actor,
        resource=draft,
        metadata={
            "name": draft.name,
            "language": draft.language,
            "category": draft.category,
            "business_account_id": str(draft.business_account_id),
            **extra,
        },
        request=request,
    )


# --------------------------------------------------------------------------- drafts


def create_draft(
    account: WhatsAppBusinessAccount,
    *,
    name: str,
    language: str,
    category: str,
    parameter_format: str,
    content: dict[str, Any],
    actor: Any,
    request: Any = None,
) -> TemplateDraft:
    try:
        with transaction.atomic():
            draft = TemplateDraft.objects.create(
                business_account=account,
                organization=account.organization,
                business=account.business,
                name=name,
                language=language,
                category=category,
                parameter_format=parameter_format,
                content=studio.normalise_content(content),
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
            )
    except IntegrityError as exc:
        raise DraftConflictError(
            "You already have a draft or template with this name in this language. "
            "Open it, or choose a different name."
        ) from exc
    _audit(actions.TEMPLATE_DRAFT_SAVED, draft, actor, request, created=True)
    return draft


def update_draft(
    draft: TemplateDraft,
    *,
    changes: dict[str, Any],
    actor: Any,
    request: Any = None,
) -> TemplateDraft:
    with transaction.atomic():
        locked = TemplateDraft.objects.select_for_update().get(pk=draft.pk)
        if locked.status != TemplateDraft.Status.DRAFT:
            raise DraftConflictError(
                "This draft was already sent to Meta, so it can no longer be edited."
            )
        for key in ("name", "language", "category", "parameter_format"):
            if key in changes:
                setattr(locked, key, changes[key])
        if "content" in changes:
            locked.content = studio.normalise_content(changes["content"])
        locked.last_error = ""
        locked.last_error_code = ""
        try:
            locked.save()
        except IntegrityError as exc:
            raise DraftConflictError(
                "You already have a draft or template with this name in this language."
            ) from exc
    _audit(actions.TEMPLATE_DRAFT_SAVED, locked, actor, request, created=False)
    return locked


def delete_draft(draft: TemplateDraft, *, actor: Any, request: Any = None) -> None:
    with transaction.atomic():
        locked = TemplateDraft.objects.select_for_update().get(pk=draft.pk)
        if locked.status != TemplateDraft.Status.DRAFT:
            raise DraftConflictError("Only a draft that was not sent to Meta can be deleted.")
        _audit(actions.TEMPLATE_DRAFT_DELETED, locked, actor, request)
        locked.delete()


# --------------------------------------------------------------------------- reconcile


def reconcile(draft: TemplateDraft) -> TemplateDraft:
    """Settle an ``unconfirmed`` (or stuck ``submitting``) draft from evidence:
    only a sync that completed AFTER the attempt can say whether Meta has it."""
    now = timezone.now()
    if (
        draft.status == TemplateDraft.Status.SUBMITTING
        and draft.last_attempt_at
        and now - draft.last_attempt_at > SUBMITTING_STALE_AFTER
    ):
        TemplateDraft.objects.filter(pk=draft.pk, status=TemplateDraft.Status.SUBMITTING).update(
            status=TemplateDraft.Status.UNCONFIRMED, updated_at=now
        )
        draft.refresh_from_db()
    if draft.status != TemplateDraft.Status.UNCONFIRMED or draft.last_attempt_at is None:
        return draft
    account = draft.business_account
    synced_at = account.templates_last_synced_at
    if synced_at is None or synced_at <= draft.last_attempt_at:
        return draft  # no evidence yet: stay unconfirmed
    found = MessageTemplate.objects.filter(
        business_account=account,
        name=draft.name,
        language=draft.language,
        missing_from_provider=False,
    ).first()
    with transaction.atomic():
        locked = TemplateDraft.objects.select_for_update().get(pk=draft.pk)
        if locked.status != TemplateDraft.Status.UNCONFIRMED:
            return locked
        if found is not None:
            locked.status = TemplateDraft.Status.SUBMITTED
            locked.template = found
            locked.submitted_at = locked.last_attempt_at
        else:
            locked.status = TemplateDraft.Status.DRAFT
            locked.last_error = "Meta did not receive this template. You can submit it again."
        locked.save()
    return locked


# --------------------------------------------------------------------------- submit


def submit_draft(
    draft: TemplateDraft,
    *,
    confirm: bool,
    content_hash: str,
    actor: Any,
    request: Any = None,
) -> TemplateDraft:
    if confirm is not True:
        raise DomainError("Confirm that you want to send this template to Meta for review.")
    draft = reconcile(draft)
    account = draft.business_account

    with transaction.atomic():
        locked = TemplateDraft.objects.select_for_update().get(pk=draft.pk)
        if locked.status == TemplateDraft.Status.SUBMITTING:
            raise DraftConflictError("This template is already being sent to Meta.")
        if locked.status == TemplateDraft.Status.SUBMITTED:
            raise DraftConflictError(
                "This template was already sent to Meta. Do not submit it again: check its status."
            )
        if locked.status == TemplateDraft.Status.UNCONFIRMED:
            raise DraftConflictError(
                "The console is not sure whether Meta received this template. Use "
                '"Check with Meta" first; it cannot be submitted again until that is settled.'
            )
        validation = run_checks(
            account,
            name=locked.name,
            language=locked.language,
            category=locked.category,
            parameter_format=locked.parameter_format,
            content=locked.content,
        )
        if not validation.ok:
            raise TemplateInvalidError(
                validation.errors[0].message,
                details={"issues": studio.issues_payload(validation.errors)},
            )
        if content_hash != draft_hash(locked, validation):
            raise DraftConflictError(
                "This template changed since you reviewed it. Review it again, then confirm."
            )
        recent = TemplateDraft.objects.filter(
            business_account=account,
            submitted_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent >= studio.CREATIONS_PER_WABA_PER_HOUR:
            raise DraftConflictError(
                f"Meta allows {studio.CREATIONS_PER_WABA_PER_HOUR} new templates per account per "
                "hour. Try again later."
            )
        now = timezone.now()
        locked.status = TemplateDraft.Status.SUBMITTING
        locked.last_attempt_at = now
        locked.attempts += 1
        locked.save()
        components = validation.components
        _audit(
            actions.TEMPLATE_SUBMIT_REQUESTED,
            locked,
            actor,
            request,
            attempt=locked.attempts,
            content_hash=content_hash[:12],
        )

    spec = TemplateSpec(
        name=locked.name,
        language=locked.language,
        category=locked.category,
        components=components,
        parameter_format=locked.parameter_format,
    )
    try:
        info = provider_for(account).create_template(account_ref(account), spec)
    except ProviderError as exc:
        ambiguous = exc.retryability == Retryability.AMBIGUOUS
        return _after_failure(
            locked,
            actor=actor,
            request=request,
            ambiguous=ambiguous,
            message=exc.message,
            code=str(exc.provider_code or exc.code),
        )
    except (ConfigurationRequiredError, PermissionError, SecretReferenceError) as exc:
        return _after_failure(
            locked,
            actor=actor,
            request=request,
            ambiguous=False,
            message="The WhatsApp connection is not available. Check the account on the Set up page.",
            code=type(exc).__name__,
        )

    template = upsert_template(account, info)
    with transaction.atomic():
        done = TemplateDraft.objects.select_for_update().get(pk=locked.pk)
        done.status = TemplateDraft.Status.SUBMITTED
        done.template = template
        done.submitted_at = timezone.now()  # after upsert: a later sync is strictly newer
        done.last_error = ""
        done.last_error_code = ""
        done.save()
        _audit(
            actions.TEMPLATE_SUBMITTED,
            done,
            actor,
            request,
            meta_status=template.status,
            template_id=str(template.pk),
        )
    return done


def _after_failure(
    draft: TemplateDraft,
    *,
    actor: Any,
    request: Any,
    ambiguous: bool,
    message: str,
    code: str,
) -> TemplateDraft:
    with transaction.atomic():
        failed = TemplateDraft.objects.select_for_update().get(pk=draft.pk)
        failed.status = (
            TemplateDraft.Status.UNCONFIRMED if ambiguous else TemplateDraft.Status.DRAFT
        )
        failed.last_error = " ".join(message.split())[:500]
        failed.last_error_code = code[:64]
        failed.save()
        _audit(
            actions.TEMPLATE_SUBMIT_FAILED,
            failed,
            actor,
            request,
            error_code=code[:64],
            outcome_unknown=ambiguous,
        )
    if ambiguous:
        raise SubmitFailedError(
            "The request to Meta did not finish, so the console cannot tell whether Meta "
            'received it. Use "Check with Meta" before trying again.',
            details={"outcome_unknown": True},
        )
    raise SubmitFailedError(
        f"Meta did not accept the template: {failed.last_error}",
        details={"provider_code": code, "outcome_unknown": False},
    )


# --------------------------------------------------------------------------- copy a template as a draft


def fields_from_template(template: MessageTemplate) -> tuple[dict[str, Any], str]:
    """Plain-English fields from a synced template, for "fix as new draft"."""
    try:
        shape = describe(template)
    except UnsupportedTemplateError as exc:
        raise DomainError(
            "This template uses features the Template Studio does not support, so it cannot be "
            f"copied here: {exc}"
        ) from exc
    if shape.header_format not in ("", "TEXT", None):
        raise DomainError(
            "This template has a media header, which the Template Studio does not support yet. "
            "Edit it in WhatsApp Manager."
        )
    examples: dict[str, str] = {}
    for spec in shape.parameters:
        if spec.component in ("header", "body") and spec.example:
            examples[spec.key] = spec.example
    buttons: list[dict[str, str]] = []
    for raw in shape.buttons:
        kind = str(raw.get("type", "")).upper()
        if kind == "QUICK_REPLY":
            buttons.append({"type": kind, "text": str(raw.get("text") or "")})
        elif kind == "URL":
            example = (raw.get("example") or [""])[0] if raw.get("example") else ""
            buttons.append(
                {
                    "type": kind,
                    "text": str(raw.get("text") or ""),
                    "url": str(raw.get("url") or ""),
                    "url_example": str(example),
                }
            )
        elif kind == "PHONE_NUMBER":
            buttons.append(
                {
                    "type": kind,
                    "text": str(raw.get("text") or ""),
                    "phone_number": str(raw.get("phone_number") or ""),
                }
            )
        else:
            raise DomainError(
                "This template has a button type the Template Studio does not support yet. "
                "Edit it in WhatsApp Manager."
            )
    content = {
        "header_text": shape.header_text if shape.header_format == "TEXT" else "",
        "body": shape.body_text,
        "examples": examples,
        "footer": shape.footer_text,
        "buttons": buttons,
    }
    return content, shape.parameter_format or "POSITIONAL"


def next_free_name(account: WhatsAppBusinessAccount, base: str, language: str) -> str:
    taken = set(
        MessageTemplate.objects.filter(business_account=account, language=language).values_list(
            "name", flat=True
        )
    ) | set(
        TemplateDraft.objects.filter(business_account=account, language=language).values_list(
            "name", flat=True
        )
    )
    stem = base.rsplit("_v", 1)[0] if base.rsplit("_v", 1)[-1].isdigit() else base
    n = 2
    while f"{stem}_v{n}" in taken:
        n += 1
    return f"{stem}_v{n}"
