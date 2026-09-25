"""Media assets: validation, storage and business-scoped access (public interface)."""

from pathlib import PurePosixPath
from typing import IO, Any

from django.db import transaction

from apps.businesses.models import Business
from apps.media import storage
from apps.media.models import MediaAsset
from apps.media.rules import MediaRule, validate_media
from core.exceptions import DomainError


class MediaRejectedError(DomainError):
    code = "MEDIA_REJECTED"
    status_code = 422


def _extension(filename: str, rule: MediaRule) -> str:
    suffix = PurePosixPath((filename or "").lower()).suffix
    return suffix if suffix in rule.extensions else rule.extensions[0]


def store_upload(
    *, business: Business, file: IO[bytes], filename: str, mime_type: str, size: int, actor: Any
) -> MediaAsset:
    """Validate an uploaded file against WhatsApp rules and store it privately."""
    file.seek(0)
    head = file.read(16)
    file.seek(0)
    from apps.media.rules import MediaValidationError

    try:
        rule = validate_media(
            mime_type=mime_type, filename=filename, size=size, head=head, outbound=True
        )
    except MediaValidationError as exc:
        raise MediaRejectedError(exc.message, details={"reason": exc.code}) from exc
    key = storage.build_key(business.organization_id, business.pk, _extension(filename, rule))
    stored = storage.save(key, file)
    return MediaAsset.objects.create(
        organization_id=business.organization_id,
        business=business,
        kind=rule.kind,
        mime_type=rule.mime_type,
        file_size=stored.size,
        sha256=stored.sha256,
        original_filename=PurePosixPath(filename or "").name[:255],
        storage_key=stored.key,
        source=MediaAsset.Source.UPLOAD,
        uploaded_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


@transaction.atomic
def store_inbound(
    *, business: Business, data: bytes, rule: MediaRule, filename: str = ""
) -> MediaAsset:
    key = storage.build_key(business.organization_id, business.pk, _extension(filename, rule))
    stored = storage.save(key, data)
    return MediaAsset.objects.create(
        organization_id=business.organization_id,
        business=business,
        kind=rule.kind,
        mime_type=rule.mime_type,
        file_size=stored.size,
        sha256=stored.sha256,
        original_filename=PurePosixPath(filename or "").name[:255],
        storage_key=stored.key,
        source=MediaAsset.Source.INBOUND,
    )
