"""Asynchronous CSV exports of factual operational data.

Large exports never run inside an HTTP request: the API creates an
``ExportJob``; a worker streams rows from the database (chunked iterator)
into a CSV stored in object storage; the owner downloads it through a
business-scoped endpoint.
"""

from django.conf import settings
from django.db import models

from apps.organizations.tenancy import TenantScopedModel


class ExportJob(TenantScopedModel):
    class Kind(models.TextChoices):
        CAMPAIGN_RECIPIENTS = "campaign_recipients", "Campaign recipients"
        CAMPAIGN_SUMMARY = "campaign_summary", "Campaign summary"
        MESSAGES = "messages", "Messages"
        COSTS = "costs", "Message costs"
        ANALYTICS_DAILY = "analytics_daily", "Daily analytics"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    business = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    params = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    row_count = models.PositiveIntegerField(null=True, blank=True)
    file_size = models.PositiveBigIntegerField(null=True, blank=True)
    storage_key = models.CharField(max_length=512, blank=True)
    filename = models.CharField(max_length=200, blank=True)
    error = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        indexes = [models.Index(fields=["business", "-created_at"], name="export_business_idx")]
