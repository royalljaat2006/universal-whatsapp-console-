"""Abstract base models shared by all domain modules."""

from django.db import models

from core.ids import uuid7


class BaseModel(models.Model):
    """UUIDv7 primary key + audit timestamps."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BackupRecord(models.Model):
    """One row per attempted database backup. Written directly by
    infrastructure/docker/backup/entrypoint.sh via ``psql`` (a plain
    BigAutoField pk, not BaseModel's UUID7, so the shell script never needs
    to generate an id itself) — this table is the backup system's audit
    trail and the source for the /metrics gauges (core.metrics, alerted on in
    infrastructure/monitoring/alerts.yml) and the platform admin API.
    System-wide, not tenant-scoped: excluded from RLS like the other system
    tables (WebhookEvent, etc.)."""

    class Status(models.TextChoices):
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    file_path = models.CharField(max_length=500, blank=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    encrypted = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    # Reported by the backup script itself (it already has filesystem access
    # to the backup volume) rather than mounting that volume into the web
    # process just to compute this.
    disk_free_bytes = models.BigIntegerField(null=True, blank=True)
    disk_total_bytes = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["-started_at"], name="backup_record_started_idx")]

    def __str__(self) -> str:
        return f"{self.status} @ {self.started_at.isoformat()}"
