"""Contacts.

A WhatsApp user can be identified by a phone number, by a business-scoped
user id (BSUID, scoped per Meta business portfolio), or both — and since 2026
the phone number may be absent entirely for users with usernames (verified,
docs/integrations/meta/api-assumptions.md#bsuid). Identities therefore live in
their own table instead of a single ``phone`` column on Contact.
"""

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models.functions import Lower, Upper

from apps.organizations.tenancy import TenantScopedModel


class Tag(TenantScopedModel):
    """Business-scoped label shared by the business's contacts and conversations."""

    business = models.ForeignKey(
        "businesses.Business", on_delete=models.CASCADE, related_name="tags"
    )
    name = models.CharField(max_length=64)
    color = models.CharField(max_length=16, blank=True)

    class Meta:
        constraints = [
            # Case-insensitive: "VIP" and "vip" are the same tag in a business.
            models.UniqueConstraint("business", Lower("name"), name="tag_business_name_ci_unique"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Contact(TenantScopedModel):
    """A WhatsApp user as known to ONE business. The same person messaging two
    businesses is two contacts: businesses must not see each other's customers."""

    class OptInStatus(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        OPTED_IN = "opted_in", "Opted in"
        OPTED_OUT = "opted_out", "Opted out"

    business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="contacts"
    )
    display_name = models.CharField(max_length=200, blank=True)
    # Name reported by WhatsApp; kept separate so agent edits are not overwritten.
    profile_name = models.CharField(max_length=200, blank=True)
    # Denormalised primary phone (E.164 digits, no '+') for search/display.
    phone_number = models.CharField(max_length=20, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    # Arbitrary tenant-defined attributes. JSONB instead of an EAV table:
    # attributes are read as a whole per contact and rarely queried across
    # contacts; a GIN index can be added when segment queries need it.
    attributes = models.JSONField(default=dict, blank=True)
    opt_in_status = models.CharField(
        max_length=16, choices=OptInStatus.choices, default=OptInStatus.UNKNOWN
    )
    opt_in_source = models.CharField(max_length=100, blank=True)
    opt_in_at = models.DateTimeField(null=True, blank=True)
    opt_out_at = models.DateTimeField(null=True, blank=True)
    tags = models.ManyToManyField(Tag, related_name="contacts", blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    # "Last seen": the contact's most recent inbound message.
    last_inbound_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["business", "phone_number"], name="contact_business_phone_idx"),
            models.Index(fields=["business", "-created_at"], name="contact_business_created_idx"),
            # Name search (icontains -> UPPER(col) LIKE ...).
            GinIndex(
                OpClass(Upper("display_name"), name="gin_trgm_ops"),
                name="contact_display_trgm_idx",
            ),
            GinIndex(
                OpClass(Upper("profile_name"), name="gin_trgm_ops"),
                name="contact_profile_trgm_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name or self.profile_name or self.phone_number or str(self.pk)


class ContactIdentity(TenantScopedModel):
    class Kind(models.TextChoices):
        PHONE = "phone", "Phone number"
        BSUID = "bsuid", "Business-scoped user id"
        PARENT_BSUID = "parent_bsuid", "Parent business-scoped user id"

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="identities")
    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    value = models.CharField(max_length=160)
    # '' for phone numbers (global); the business portfolio for BSUIDs.
    scope = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        constraints = [
            # Idempotent contact resolution under concurrent webhooks.
            models.UniqueConstraint(
                fields=["business", "kind", "scope", "value"],
                name="contact_identity_business_unique",
            ),
        ]
        verbose_name_plural = "contact identities"

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"


class ContactImportJob(TenantScopedModel):
    """One CSV upload, processed asynchronously (mirrors apps.reports.ExportJob
    for the read direction). ``row_errors`` is capped (see apps.contacts.
    import_csv.MAX_REPORTED_ERRORS) — a huge bad file reports its first N
    errors, not an unbounded list."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        SUCCEEDED_WITH_ERRORS = "succeeded_with_errors", "Succeeded with errors"
        FAILED = "failed", "Failed"

    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="+")
    storage_key = models.CharField(max_length=512, blank=True)
    filename = models.CharField(max_length=200, blank=True)
    # Applied to every row imported by this job, in addition to any per-row
    # "tags" column. Blank means no job-wide tag.
    tag_name = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    total_rows = models.PositiveIntegerField(null=True, blank=True)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    row_errors = models.JSONField(default=list, blank=True)
    error = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        indexes = [
            models.Index(fields=["business", "-created_at"], name="contact_import_business_idx")
        ]


class ContactNote(TenantScopedModel):
    """Internal note about a contact (never sent to the customer)."""

    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="+")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="note_entries")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    body = models.TextField(max_length=5000)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["contact", "-created_at"], name="contact_note_idx")]
