"""Template Studio API.

GET    /api/v1/templates/studio/options/           categories, languages, limits, starters
POST   /api/v1/templates/studio/validate/          check content (nothing saved, nothing sent)
GET    /api/v1/templates/drafts/                   drafts of the selected business
POST   /api/v1/templates/drafts/                   save a new draft (local only)
GET    /api/v1/templates/drafts/<id>/              draft + live checks + status
PATCH  /api/v1/templates/drafts/<id>/              edit a draft that was not sent
DELETE /api/v1/templates/drafts/<id>/              delete a draft that was not sent
POST   /api/v1/templates/drafts/<id>/submit/       {"confirm": true, "content_hash": "..."}: send to Meta
POST   /api/v1/templates/drafts/<id>/check/        ask Meta again (template sync) and reconcile
GET    /api/v1/templates/<id>/guidance/            plain status + recovery guidance
POST   /api/v1/templates/<id>/copy-to-draft/       a corrected copy as a new draft

Read needs ``templates.view``; everything that changes or sends needs
``templates.manage``. Every lookup is tenant- and business-scoped: a foreign or
out-of-scope id is 404. See ``studio.py`` (rules) and ``studio_service.py``.
"""

from typing import Any

from django.db import transaction
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.templates import guidance, studio, studio_service
from apps.templates.models import MessageTemplate, TemplateDraft
from apps.whatsapp.models import WhatsAppBusinessAccount
from core.exceptions import NotFoundError


class _Scoped(OrganizationScopedMixin, APIView):
    def account(self, pk: Any) -> WhatsAppBusinessAccount:
        qs = self.restrict_to_businesses(
            WhatsAppBusinessAccount.objects.for_organization(self.organization).select_related(
                "business"
            ),
            "business",
        )
        if self.business is not None:
            qs = qs.filter(business=self.business)
        found = qs.filter(pk=pk).first()
        if found is None:
            raise NotFoundError("WhatsApp Business Account not found.")
        return found

    def drafts(self) -> Any:
        qs = self.restrict_to_businesses(
            TemplateDraft.objects.for_organization(self.organization).select_related(
                "business_account", "template"
            ),
            "business",
        )
        if self.business is not None:
            qs = qs.filter(business=self.business)
        return qs

    def draft(self, pk: Any) -> TemplateDraft:
        found = self.drafts().filter(pk=pk).first()
        if found is None:
            raise NotFoundError("Draft not found.")
        return found

    def template(self, pk: Any) -> MessageTemplate:
        qs = self.restrict_to_businesses(
            MessageTemplate.objects.for_organization(self.organization).select_related(
                "business_account"
            ),
            "business",
        )
        found = qs.filter(pk=pk).first()
        if found is None:
            raise NotFoundError("Template not found.")
        return found


def draft_payload(draft: TemplateDraft, *, include_components: bool = False) -> dict[str, Any]:
    validation = studio_service.run_checks(
        draft.business_account,
        name=draft.name,
        language=draft.language,
        category=draft.category,
        parameter_format=draft.parameter_format,
        content=draft.content,
    )
    if draft.status != TemplateDraft.Status.DRAFT:
        # Once sent, the template existing at Meta is expected, not a naming problem.
        validation.errors = [
            e for e in validation.errors if e.code not in {"name_taken", "duplicate_content"}
        ]
    payload: dict[str, Any] = {
        "id": str(draft.pk),
        "business_account_id": str(draft.business_account_id),
        "business_id": str(draft.business_id),
        "name": draft.name,
        "language": draft.language,
        "category": draft.category,
        "parameter_format": draft.parameter_format,
        "content": draft.content,
        "status": draft.status,
        "status_label": draft.get_status_display(),
        "template_id": str(draft.template_id) if draft.template_id else None,
        "attempts": draft.attempts,
        "submitted_at": draft.submitted_at,
        "last_attempt_at": draft.last_attempt_at,
        "last_error": draft.last_error,
        "last_error_code": draft.last_error_code,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
        "checks": {
            "ok": validation.ok,
            "errors": studio.issues_payload(validation.errors),
            "warnings": studio.issues_payload(validation.warnings),
            "counts": validation.counts,
            "preview": validation.preview,
        },
        "content_hash": studio_service.draft_hash(draft, validation),
        "can_submit": draft.status == TemplateDraft.Status.DRAFT and validation.ok,
        "guidance": guidance.explain(draft.template, draft),
    }
    if include_components:
        payload["components"] = validation.components
    return payload


class StudioOptionsView(_Scoped):
    required_permissions = {"get": "templates.view"}

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(
            {
                "categories": studio.CATEGORIES,
                "languages": [{"value": c, "label": n} for c, n in studio.LANGUAGES],
                "parameter_formats": ["POSITIONAL", "NAMED"],
                "button_types": [
                    {"value": "QUICK_REPLY", "label": "Quick reply", "max": studio.QUICK_REPLY_MAX},
                    {"value": "URL", "label": "Website", "max": studio.URL_BUTTONS_MAX},
                    {"value": "PHONE_NUMBER", "label": "Call", "max": studio.PHONE_BUTTONS_MAX},
                ],
                "limits": {
                    "name": studio.NAME_MAX,
                    "header": studio.HEADER_TEXT_MAX,
                    "body": studio.BODY_MAX,
                    "footer": studio.FOOTER_MAX,
                    "buttons": studio.BUTTONS_MAX,
                    "button_text": studio.BUTTON_TEXT_MAX,
                    "creations_per_hour": studio.CREATIONS_PER_WABA_PER_HOUR,
                    "review_hours": studio.REVIEW_HOURS,
                },
                "unsupported": [
                    "Authentication (one-time code) templates",
                    "Image, video, document or location headers",
                    "Copy-code and other button types",
                ],
                "starters": studio.STARTERS,
                "source": "Meta's template documentation, read 2026-10-12",
            }
        )


class ValidateSerializer(serializers.Serializer):
    business_account_id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True, max_length=600)
    language = serializers.CharField(allow_blank=True, max_length=16)
    category = serializers.CharField(allow_blank=True, max_length=32)
    parameter_format = serializers.ChoiceField(
        choices=["POSITIONAL", "NAMED"], default="POSITIONAL"
    )
    content = serializers.DictField()


class StudioValidateView(_Scoped):
    required_permissions = {"post": "templates.manage"}

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        body = ValidateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        account = self.account(data["business_account_id"])
        validation = studio_service.run_checks(
            account,
            name=data["name"],
            language=data["language"],
            category=data["category"],
            parameter_format=data["parameter_format"],
            content=data["content"],
        )
        return Response(
            {
                "ok": validation.ok,
                "errors": studio.issues_payload(validation.errors),
                "warnings": studio.issues_payload(validation.warnings),
                "counts": validation.counts,
                "preview": validation.preview,
                "components": validation.components,
            }
        )


class DraftWriteSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True, max_length=600)
    language = serializers.CharField(max_length=16)
    category = serializers.CharField(max_length=32)
    parameter_format = serializers.ChoiceField(
        choices=["POSITIONAL", "NAMED"], default="POSITIONAL"
    )
    content = serializers.DictField()


class DraftListCreateView(_Scoped):
    required_permissions = {"get": "templates.view", "post": "templates.manage"}

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        rows = self.drafts().order_by("-updated_at")[:100]
        return Response({"results": [draft_payload(d) for d in rows]})

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        body = DraftWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        account_id = request.data.get("business_account_id")
        if not account_id:
            raise serializers.ValidationError({"business_account_id": "required"})
        account = self.account(account_id)
        draft = studio_service.create_draft(
            account,
            name=data["name"],
            language=data["language"],
            category=data["category"],
            parameter_format=data["parameter_format"],
            content=data["content"],
            actor=request.user,
            request=request,
        )
        return Response(draft_payload(draft), status=status.HTTP_201_CREATED)


class DraftDetailView(_Scoped):
    required_permissions = {
        "get": "templates.view",
        "patch": "templates.manage",
        "delete": "templates.manage",
    }

    def get(self, request: Request, pk: str) -> Response:
        draft = studio_service.reconcile(self.draft(pk))
        return Response(draft_payload(draft, include_components=True))

    def patch(self, request: Request, pk: str) -> Response:
        draft = self.draft(pk)
        body = DraftWriteSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        updated = studio_service.update_draft(
            draft, changes=dict(body.validated_data), actor=request.user, request=request
        )
        return Response(draft_payload(updated))

    def delete(self, request: Request, pk: str) -> Response:
        studio_service.delete_draft(self.draft(pk), actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SubmitSerializer(serializers.Serializer):
    confirm = serializers.BooleanField()
    content_hash = serializers.CharField(max_length=64)


class DraftSubmitView(_Scoped):
    required_permissions = {"post": "templates.manage"}

    def post(self, request: Request, pk: str) -> Response:
        body = SubmitSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        draft = self.draft(pk)
        done = studio_service.submit_draft(
            draft,
            confirm=body.validated_data["confirm"],
            content_hash=body.validated_data["content_hash"],
            actor=request.user,
            request=request,
        )
        payload = draft_payload(done)
        payload["note"] = (
            "Sent to Meta. This is NOT an approval: Meta reviews it and reports the result, "
            "usually within 24 hours."
        )
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class DraftCheckView(_Scoped):
    """Ask Meta again: queue a template sync for the account, then reconcile
    from whatever evidence already exists."""

    required_permissions = {"post": "templates.manage"}

    def post(self, request: Request, pk: str) -> Response:
        from apps.templates.tasks import sync_business_account_templates

        draft = self.draft(pk)
        account_id = str(draft.business_account_id)
        transaction.on_commit(lambda: sync_business_account_templates.delay(account_id))
        return Response(
            {
                "status": "queued",
                "note": "Asking Meta for the latest status. Check back in a moment.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class TemplateGuidanceView(_Scoped):
    required_permissions = {"get": "templates.view"}

    def get(self, request: Request, pk: str) -> Response:
        template = self.template(pk)
        draft = template.drafts.order_by("-created_at").first()
        return Response(guidance.explain(template, draft))


class CopyToDraftView(_Scoped):
    required_permissions = {"post": "templates.manage"}

    def post(self, request: Request, pk: str) -> Response:
        template = self.template(pk)
        account = self.account(template.business_account_id)
        content, fmt = studio_service.fields_from_template(template)
        category = (
            template.category if template.category in studio.SUPPORTED_CATEGORIES else "UTILITY"
        )
        name = studio_service.next_free_name(account, template.name, template.language)
        draft = studio_service.create_draft(
            account,
            name=name,
            language=template.language,
            category=category,
            parameter_format=fmt,
            content=content,
            actor=request.user,
            request=request,
        )
        return Response(draft_payload(draft), status=status.HTTP_201_CREATED)
