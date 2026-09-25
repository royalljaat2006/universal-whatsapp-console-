"""WhatsApp Business Accounts, phone numbers and provider credentials.

Hierarchy (verified, see docs/integrations/meta/): Meta Business Portfolio ->
WABA (billing unit, owns templates) -> phone numbers (sender identities).
Messaging limits are shared at portfolio level, so the portfolio id is kept
on the WABA for limit/BSUID scoping.
"""

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.organizations.tenancy import TenantScopedModel
from core import secrets


class Provider(models.TextChoices):
    META = "meta", "Meta WhatsApp Cloud API"


class ProviderCredential(TenantScopedModel):
    """A *reference* to a provider secret — never the secret in a plain column.

    ``secret_reference`` (``env:PROVIDER_SECRET_*``) points at a secret owned by
    the deployment's secret store; ``encrypted_secret`` holds Fernet ciphertext
    when the tenant supplies the token through the product. Exactly one is set.
    """

    class Kind(models.TextChoices):
        SYSTEM_USER_TOKEN = "system_user_token", "System user access token"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"

    provider = models.CharField(max_length=32, choices=Provider.choices)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    label = models.CharField(max_length=100)
    secret_reference = models.CharField(max_length=200, blank=True)
    encrypted_secret = models.BinaryField(null=True, blank=True, editable=False)
    # Non-secret identification of the token (e.g. last 4 chars) for operators.
    fingerprint = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(secret_reference="") & models.Q(encrypted_secret__isnull=False))
                    | (~models.Q(secret_reference="") & models.Q(encrypted_secret__isnull=True))
                ),
                name="credential_exactly_one_secret_source",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.provider})"

    def clean(self) -> None:
        if self.secret_reference:
            try:
                secrets.SecretReference.parse(self.secret_reference)
            except secrets.SecretReferenceError as exc:
                raise ValidationError({"secret_reference": str(exc)}) from exc

    def resolve_secret(self) -> str:
        """Return the plaintext secret. Call only at the point of use; never
        log, serialize or cache the result."""
        if self.status != self.Status.ACTIVE:
            raise PermissionError("credential is revoked")
        if self.secret_reference:
            return secrets.resolve_reference(self.secret_reference)
        return secrets.resolve_encrypted(self.encrypted_secret)


class ConnectionState(models.TextChoices):
    PENDING = "pending", "Pending verification"
    CONNECTED = "connected", "Connected"
    ERROR = "error", "Error"
    DISCONNECTED = "disconnected", "Disconnected"


class SyncState(models.TextChoices):
    NEVER = "never", "Never synced"
    SYNCING = "syncing", "Syncing"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class WhatsAppBusinessAccount(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISCONNECTED = "disconnected", "Disconnected"
        RESTRICTED = "restricted", "Restricted"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="business_accounts",
    )
    provider = models.CharField(max_length=32, choices=Provider.choices, default=Provider.META)
    external_id = models.CharField(max_length=64, help_text="WABA ID at the provider")
    business_portfolio_external_id = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=200, blank=True)
    credential = models.ForeignKey(
        ProviderCredential, on_delete=models.PROTECT, related_name="business_accounts"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    currency = models.CharField(max_length=3, blank=True)
    timezone_id = models.CharField(max_length=64, blank=True)
    webhooks_subscribed_at = models.DateTimeField(null=True, blank=True)
    # Connection verification (credential valid + WABA reachable at the provider).
    connection_state = models.CharField(
        max_length=16, choices=ConnectionState.choices, default=ConnectionState.PENDING
    )
    connection_error = models.CharField(max_length=500, blank=True)
    connection_error_code = models.CharField(max_length=64, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    # Phone-number synchronisation from the provider.
    sync_state = models.CharField(max_length=16, choices=SyncState.choices, default=SyncState.NEVER)
    sync_error = models.CharField(max_length=500, blank=True)
    sync_started_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    # Provider-reported attributes (verbatim), overwritten only by sync.
    account_review_status = models.CharField(max_length=32, blank=True)
    business_verification_status = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=8, blank=True)
    ownership_type = models.CharField(max_length=32, blank=True)
    message_template_namespace = models.CharField(max_length=100, blank=True)
    provider_metadata = models.JSONField(default=dict, blank=True)
    # Template synchronisation (GET /{WABA_ID}/message_templates).
    template_sync_state = models.CharField(
        max_length=16, choices=SyncState.choices, default=SyncState.NEVER
    )
    template_sync_error = models.CharField(max_length=500, blank=True)
    templates_last_synced_at = models.DateTimeField(null=True, blank=True)
    # Local-only attributes, never touched by provider sync.
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            # Global (not per-tenant): inbound webhooks are routed to a tenant
            # by WABA id, so one WABA may belong to exactly one organization.
            models.UniqueConstraint(
                fields=["provider", "external_id"], name="waba_provider_external_unique"
            ),
        ]
        verbose_name = "WhatsApp Business Account"

    def __str__(self) -> str:
        return self.name or self.external_id

    @property
    def bsuid_scope(self) -> str:
        """BSUIDs are unique per business portfolio. When the portfolio id is
        unknown we fall back to the WABA id (documented limitation)."""
        return self.business_portfolio_external_id or f"waba:{self.external_id}"


class WhatsAppPhoneNumber(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending registration"
        CONNECTED = "connected", "Connected"
        DISCONNECTED = "disconnected", "Disconnected"
        FLAGGED = "flagged", "Flagged"
        RESTRICTED = "restricted", "Restricted"

    business_account = models.ForeignKey(
        WhatsAppBusinessAccount, on_delete=models.PROTECT, related_name="phone_numbers"
    )
    # Denormalised from the WABA for business-scoped queries and isolation.
    business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="phone_numbers"
    )
    provider = models.CharField(max_length=32, choices=Provider.choices, default=Provider.META)
    external_id = models.CharField(max_length=64, help_text="Phone Number ID at the provider")
    # Local-only label (e.g. "Support line"); never overwritten by sync.
    label = models.CharField(max_length=100, blank=True)
    display_phone_number = models.CharField(max_length=32)
    verified_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Raw provider values kept verbatim: their vocabularies change over time.
    provider_status = models.CharField(max_length=64, blank=True)
    quality_rating = models.CharField(max_length=32, blank=True)
    name_status = models.CharField(max_length=64, blank=True)
    code_verification_status = models.CharField(max_length=64, blank=True)
    messaging_limit_tier = models.CharField(max_length=64, blank=True)
    # Outbound throttle for this sender (msg/s). Meta default is 80.
    throughput_mps = models.PositiveIntegerField(null=True, blank=True)
    business_profile = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    provider_metadata = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            # Global uniqueness: inbound messages are routed to a tenant by
            # metadata.phone_number_id.
            models.UniqueConstraint(
                fields=["provider", "external_id"], name="phone_provider_external_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["business", "status"], name="phone_business_status_idx"),
        ]
        verbose_name = "WhatsApp phone number"

    def __str__(self) -> str:
        return self.display_phone_number

    @property
    def effective_throughput_mps(self) -> int:
        return self.throughput_mps or settings.WHATSAPP_DEFAULT_THROUGHPUT_MPS

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Denormalised tenant and business must always match the owning WABA.
        self.organization_id = self.business_account.organization_id
        self.business_id = self.business_account.business_id
        super().save(*args, **kwargs)


class WhatsAppAccountEvent(TenantScopedModel):
    """Normalised account/number notification from the provider (quality and
    limit changes, account alerts, account updates, capability changes).

    ``payload`` keeps the provider value verbatim; only documented fields are
    interpreted. ``dedupe_key`` makes webhook redelivery and replay idempotent.
    """

    class Severity(models.TextChoices):
        INFO = "info", "Informational"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    business = models.ForeignKey(
        "businesses.Business", on_delete=models.CASCADE, related_name="account_events"
    )
    business_account = models.ForeignKey(
        WhatsAppBusinessAccount, on_delete=models.CASCADE, related_name="events"
    )
    phone_number = models.ForeignKey(
        WhatsAppPhoneNumber, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    field = models.CharField(max_length=64)
    event = models.CharField(max_length=100, blank=True)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.INFO)
    summary = models.CharField(max_length=500, blank=True)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField()
    dedupe_key = models.CharField(max_length=64, unique=True)
    source_event_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["business", "-occurred_at"], name="account_event_business_idx"),
            models.Index(
                fields=["business_account", "-occurred_at"], name="account_event_waba_idx"
            ),
        ]
