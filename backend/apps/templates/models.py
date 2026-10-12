"""Message templates (owned by a WABA at the provider).

Components are stored as JSONB in the provider's component schema rather
than normalised TemplateComponent/TemplateVariable tables: the schema is rich
(headers, media, buttons, carousels, named vs positional params), evolves
between API versions, and is always read/written as a whole. Variables are
derived from components when validating a send.
"""

from typing import Any

from django.db import models

from apps.organizations.tenancy import TenantScopedModel


class MessageTemplate(TenantScopedModel):
    business_account = models.ForeignKey(
        "whatsapp.WhatsAppBusinessAccount", on_delete=models.PROTECT, related_name="templates"
    )
    business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="templates"
    )
    external_id = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=512)
    language = models.CharField(max_length=16)
    # Provider-native values kept verbatim (e.g. MARKETING / UTILITY /
    # AUTHENTICATION; APPROVED / PENDING_REVIEW / REJECTED / PAUSED ...).
    category = models.CharField(max_length=32)
    status = models.CharField(max_length=32)
    parameter_format = models.CharField(max_length=16, blank=True)
    components = models.JSONField(default=list, blank=True)
    quality_score = models.CharField(max_length=32, blank=True)
    rejected_reason = models.CharField(max_length=255, blank=True)
    last_status_event_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    # Not returned by the provider in the latest full sync (deleted at Meta).
    missing_from_provider = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business_account", "name", "language"],
                name="template_waba_name_lang_unique",
            ),
            models.UniqueConstraint(
                fields=["business_account", "external_id"],
                condition=~models.Q(external_id=""),
                name="template_waba_external_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="template_org_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.language}]"

    @property
    def is_sendable(self) -> bool:
        return self.status == "APPROVED"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.organization_id = self.business_account.organization_id
        self.business_id = self.business_account.business_id
        super().save(*args, **kwargs)


class MessageTemplateVersion(TenantScopedModel):
    """Immutable snapshot recorded whenever synced/edited content or status
    changes, so a historical message can be matched to the exact content."""

    template = models.ForeignKey(MessageTemplate, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    category = models.CharField(max_length=32)
    status = models.CharField(max_length=32)
    components = models.JSONField(default=list)
    content_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["template", "version"], name="template_version_unique"),
        ]
        ordering = ["-version"]


class TemplateDraft(TenantScopedModel):
    """A template being written in the Template Studio. Local only: nothing
    here has been seen by Meta until ``submit`` succeeds, and even then the
    template's real status is whatever Meta later reports (never inferred).

    ``content`` holds the plain-English fields (header, body, examples, footer,
    buttons); the provider ``components`` are always rebuilt from it by
    ``apps.templates.studio`` so the preview, the checks and what is submitted
    cannot drift apart.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (not sent to Meta)"
        SUBMITTING = "submitting", "Sending to Meta"
        SUBMITTED = "submitted", "Sent to Meta"
        # The request may or may not have reached Meta (timeout, crash). Blocked
        # from re-submission until a sync shows whether the template exists.
        UNCONFIRMED = "unconfirmed", "Not sure if Meta received it"

    business_account = models.ForeignKey(
        "whatsapp.WhatsAppBusinessAccount", on_delete=models.PROTECT, related_name="template_drafts"
    )
    business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="template_drafts"
    )
    name = models.CharField(max_length=512)
    language = models.CharField(max_length=16)
    category = models.CharField(max_length=32)
    parameter_format = models.CharField(max_length=16, default="POSITIONAL")
    content = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    template = models.ForeignKey(
        MessageTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="drafts"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    # What Meta said when it refused (its own wording, trimmed) and its code.
    last_error = models.CharField(max_length=500, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business_account", "name", "language"],
                name="template_draft_waba_name_lang_unique",
            ),
        ]
        indexes = [models.Index(fields=["organization", "status"], name="tpl_draft_org_status_idx")]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.organization_id = self.business_account.organization_id
        self.business_id = self.business_account.business_id
        super().save(*args, **kwargs)
