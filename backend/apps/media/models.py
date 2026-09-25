"""Stored media files (uploads for sending, and downloaded inbound media).

Only metadata lives in PostgreSQL; the bytes live in the configured storage
backend (local volume in development, S3-compatible object storage in
production — see apps/media/storage.py). Every asset belongs to one business,
and content is only served through an authorized API endpoint.
"""

from django.conf import settings
from django.db import models

from apps.organizations.tenancy import TenantScopedModel


class MediaAsset(TenantScopedModel):
    class Source(models.TextChoices):
        UPLOAD = "upload", "Uploaded by a user"
        INBOUND = "inbound", "Received from WhatsApp"

    business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="media_assets"
    )
    kind = models.CharField(max_length=16)
    mime_type = models.CharField(max_length=128)
    file_size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    original_filename = models.CharField(max_length=255, blank=True)
    storage_key = models.CharField(max_length=512, unique=True)
    source = models.CharField(max_length=16, choices=Source.choices)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        indexes = [
            models.Index(fields=["business", "-created_at"], name="media_business_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.original_filename or self.pk}"
