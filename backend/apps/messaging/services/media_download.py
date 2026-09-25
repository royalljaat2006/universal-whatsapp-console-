"""Inbound media download (worker side; never inside the webhook request).

webhook -> MessageAttachment(pending, provider_media_id) -> [media queue]
-> GET /{MEDIA_ID} (fresh URL, valid 5 min) -> download (allow-listed host,
size-capped) -> private storage -> MediaAsset -> attachment STORED
-> realtime ``message.media_ready``.

Failure handling:
- expired URL (404 on download) / transient provider or network errors /
  storage outage: retried with backoff (a new URL is requested each attempt);
- media id unknown or expired (webhook media ids live 7 days), unsupported
  type, oversize: failed permanently with a reason on the attachment;
- duplicate webhooks/replays never re-download a stored attachment.

Checksums: the provider's ``sha256`` is recorded as reported, but its
encoding is not documented precisely enough to enforce, so we store our own
SHA-256 of the bytes and log a mismatch instead of discarding the file.
"""

import base64
import hashlib
import logging
from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.db.models import F

from apps.media.rules import rule_for_mime
from apps.media.services import store_inbound
from apps.media.storage import MediaStorageError
from apps.messaging.models import MessageAttachment
from apps.messaging.services import events
from apps.whatsapp.services import provider_for, sender_ref
from core.exceptions import ConfigurationRequiredError
from providers.whatsapp.errors import ProviderError, Retryability
from workers.retry import backoff_seconds

logger = logging.getLogger("messaging.media")

MAX_DOWNLOAD_ATTEMPTS = 6


@dataclass(frozen=True)
class DownloadOutcome:
    status: str
    retry_in: float | None = None


def _mark_failed(attachment_id: UUID, code: str, reason: str) -> DownloadOutcome:
    MessageAttachment.objects.filter(pk=attachment_id).update(
        storage_status=MessageAttachment.StorageStatus.FAILED, last_error=f"{code}: {reason}"[:500]
    )
    attachment = MessageAttachment.objects.select_related("message").get(pk=attachment_id)
    events.media_updated(attachment)
    logger.warning(
        "inbound media failed", extra={"attachment_id": str(attachment_id), "code": code}
    )
    return DownloadOutcome(status="failed")


def _retry(
    attachment: MessageAttachment, code: str, reason: str, hint: float | None = None
) -> DownloadOutcome:
    if attachment.download_attempts >= MAX_DOWNLOAD_ATTEMPTS:
        return _mark_failed(attachment.pk, code, reason)
    MessageAttachment.objects.filter(pk=attachment.pk).update(last_error=f"{code}: {reason}"[:500])
    return DownloadOutcome(
        status="retry", retry_in=backoff_seconds(attachment.download_attempts, hint=hint)
    )


def _checksum_matches(reported: str, data: bytes) -> bool:
    digest = hashlib.sha256(data).digest()
    return reported in {digest.hex(), base64.b64encode(digest).decode()}


def download_inbound_attachment(attachment_id: UUID | str) -> DownloadOutcome:
    with transaction.atomic():
        attachment = (
            MessageAttachment.objects.select_for_update()
            .select_related(
                "message__phone_number__business_account__credential", "message__business"
            )
            .filter(pk=attachment_id)
            .first()
        )
        if attachment is None:
            return DownloadOutcome(status="missing")
        if attachment.storage_status == MessageAttachment.StorageStatus.STORED:
            return DownloadOutcome(status="skipped")
        if not attachment.provider_media_id:
            return _mark_failed(attachment.pk, "MEDIA_ID_MISSING", "no provider media id")
        MessageAttachment.objects.filter(pk=attachment.pk).update(
            download_attempts=F("download_attempts") + 1
        )
        attachment.refresh_from_db(fields=["download_attempts"])

    message = attachment.message
    number = message.phone_number
    try:
        sender = sender_ref(number)
    except (ConfigurationRequiredError, PermissionError) as exc:
        return _retry(attachment, "CONFIGURATION_REQUIRED", str(exc))

    provider = provider_for(number)
    try:
        info = provider.get_media(sender, attachment.provider_media_id)
    except ProviderError as exc:
        if exc.is_retryable or exc.retryability == Retryability.AMBIGUOUS:
            return _retry(attachment, exc.code, exc.message, exc.retry_after_seconds)
        # Unknown/expired media id (webhook media ids live 7 days).
        return _mark_failed(attachment.pk, "MEDIA_UNAVAILABLE", exc.message)

    rule = rule_for_mime(info.mime_type or attachment.mime_type)
    if rule is None:
        return _mark_failed(
            attachment.pk, "UNSUPPORTED_MEDIA_TYPE", info.mime_type or attachment.mime_type
        )
    try:
        data = provider.download_media(sender, info.url, max_bytes=rule.max_bytes)
    except ProviderError as exc:
        if exc.is_retryable:
            return _retry(attachment, exc.code, exc.message, exc.retry_after_seconds)
        return _mark_failed(attachment.pk, exc.code, exc.message)

    reported = info.sha256 or attachment.sha256
    if reported and not _checksum_matches(reported, data):
        logger.warning(
            "inbound media checksum differs from provider value",
            extra={"attachment_id": str(attachment.pk)},
        )
    try:
        asset = store_inbound(
            business=message.business, data=data, rule=rule, filename=attachment.filename
        )
    except MediaStorageError as exc:
        return _retry(attachment, "MEDIA_STORAGE_FAILED", str(exc))

    with transaction.atomic():
        MessageAttachment.objects.filter(pk=attachment.pk).update(
            media_asset=asset,
            storage_key=asset.storage_key,
            storage_status=MessageAttachment.StorageStatus.STORED,
            mime_type=rule.mime_type,
            file_size=asset.file_size,
            last_error="",
        )
        attachment.refresh_from_db()
        events.media_updated(attachment)
    logger.info(
        "inbound media stored",
        extra={"attachment_id": str(attachment.pk), "bytes": asset.file_size},
    )
    return DownloadOutcome(status="stored")


def enqueue_download(attachment_id: UUID) -> None:
    from apps.messaging.tasks import download_inbound_media

    try:
        download_inbound_media.delay(str(attachment_id))
    except Exception:
        # Broker down: pending attachments are picked up by the media sweeper.
        logger.exception(
            "could not enqueue media download", extra={"attachment_id": str(attachment_id)}
        )


def requeue_pending_downloads(limit: int = 200) -> int:
    from datetime import timedelta

    from django.utils import timezone

    stale = list(
        MessageAttachment.objects.filter(
            storage_status=MessageAttachment.StorageStatus.PENDING,
            message__direction="inbound",
            updated_at__lt=timezone.now() - timedelta(minutes=30),
        ).values_list("pk", flat=True)[:limit]
    )
    for attachment_id in stale:
        enqueue_download(attachment_id)
    return len(stale)
