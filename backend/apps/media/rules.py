"""WhatsApp media rules — the single place that knows which files are allowed.

Verified 2026-09-23 against Meta's "Supported media types" reference
(docs/integrations/meta/api-assumptions.md#media). If Meta changes the table,
only this module changes.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

KB = 1024
MB = 1024 * KB


class MediaKind(StrEnum):
    IMAGE = "image"
    DOCUMENT = "document"
    VIDEO = "video"
    AUDIO = "audio"
    STICKER = "sticker"


@dataclass(frozen=True)
class MediaRule:
    kind: MediaKind
    mime_type: str
    extensions: tuple[str, ...]
    max_bytes: int
    # Leading-bytes signatures used to verify the file matches its claimed type
    # (None = no reliable signature, e.g. plain text).
    signatures: tuple[bytes, ...] | None


_ZIP = (b"PK\x03\x04",)  # docx/xlsx/pptx are ZIP containers
_OLE = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)  # legacy doc/xls/ppt

RULES: tuple[MediaRule, ...] = (
    # Audio (16 MB)
    MediaRule(MediaKind.AUDIO, "audio/aac", (".aac",), 16 * MB, (b"\xff\xf1", b"\xff\xf9")),
    MediaRule(MediaKind.AUDIO, "audio/amr", (".amr",), 16 * MB, (b"#!AMR",)),
    MediaRule(
        MediaKind.AUDIO,
        "audio/mpeg",
        (".mp3",),
        16 * MB,
        (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ),
    MediaRule(MediaKind.AUDIO, "audio/mp4", (".m4a",), 16 * MB, None),  # ftyp box checked below
    MediaRule(MediaKind.AUDIO, "audio/ogg", (".ogg",), 16 * MB, (b"OggS",)),  # OPUS codec only
    # Documents (100 MB)
    MediaRule(MediaKind.DOCUMENT, "text/plain", (".txt",), 100 * MB, None),
    MediaRule(MediaKind.DOCUMENT, "application/pdf", (".pdf",), 100 * MB, (b"%PDF",)),
    MediaRule(MediaKind.DOCUMENT, "application/msword", (".doc",), 100 * MB, _OLE),
    MediaRule(
        MediaKind.DOCUMENT,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        (".docx",),
        100 * MB,
        _ZIP,
    ),
    MediaRule(MediaKind.DOCUMENT, "application/vnd.ms-excel", (".xls",), 100 * MB, _OLE),
    MediaRule(
        MediaKind.DOCUMENT,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        (".xlsx",),
        100 * MB,
        _ZIP,
    ),
    MediaRule(MediaKind.DOCUMENT, "application/vnd.ms-powerpoint", (".ppt",), 100 * MB, _OLE),
    MediaRule(
        MediaKind.DOCUMENT,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        (".pptx",),
        100 * MB,
        _ZIP,
    ),
    # Images (5 MB)
    MediaRule(MediaKind.IMAGE, "image/jpeg", (".jpg", ".jpeg"), 5 * MB, (b"\xff\xd8\xff",)),
    MediaRule(MediaKind.IMAGE, "image/png", (".png",), 5 * MB, (b"\x89PNG\r\n\x1a\n",)),
    # Stickers (WebP; 500 KB animated / 100 KB static — the larger bound is
    # enforced here, Meta enforces the static limit on send)
    MediaRule(MediaKind.STICKER, "image/webp", (".webp",), 500 * KB, (b"RIFF",)),
    # Video (16 MB)
    MediaRule(MediaKind.VIDEO, "video/mp4", (".mp4",), 16 * MB, None),  # ftyp box
    MediaRule(MediaKind.VIDEO, "video/3gpp", (".3gp",), 16 * MB, None),  # ftyp box
)

_BY_MIME = {rule.mime_type: rule for rule in RULES}
# Kinds a user may attach from the Inbox (stickers are receive-only here).
OUTBOUND_KINDS = frozenset({MediaKind.IMAGE, MediaKind.DOCUMENT, MediaKind.VIDEO, MediaKind.AUDIO})
MAX_UPLOAD_BYTES = max(rule.max_bytes for rule in RULES)


class MediaValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_mime(mime_type: str) -> str:
    return (mime_type or "").split(";", 1)[0].strip().lower()


def rule_for_mime(mime_type: str) -> MediaRule | None:
    return _BY_MIME.get(normalize_mime(mime_type))


def _signature_ok(rule: MediaRule, head: bytes) -> bool:
    if rule.mime_type in {"audio/mp4", "video/mp4", "video/3gpp"}:
        return len(head) >= 12 and head[4:8] == b"ftyp"
    if rule.mime_type == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if rule.mime_type == "text/plain":
        return b"\x00" not in head  # reject binary masquerading as text
    if rule.signatures is None:
        return True
    return any(head.startswith(sig) for sig in rule.signatures)


def validate_media(
    *, mime_type: str, filename: str, size: int, head: bytes, outbound: bool
) -> MediaRule:
    """Validate a file against the WhatsApp rules: known MIME type, matching
    extension (uploads), size limit, and leading-byte signature."""
    rule = rule_for_mime(mime_type)
    if rule is None:
        raise MediaValidationError(
            "UNSUPPORTED_MEDIA_TYPE", f"{mime_type or 'unknown'} is not supported by WhatsApp"
        )
    if outbound and rule.kind not in OUTBOUND_KINDS:
        raise MediaValidationError(
            "UNSUPPORTED_MEDIA_TYPE", f"{rule.kind} cannot be sent from the console"
        )
    if outbound:
        suffix = PurePosixPath((filename or "").lower()).suffix
        if suffix not in rule.extensions:
            raise MediaValidationError(
                "MEDIA_EXTENSION_MISMATCH",
                f"file extension {suffix or '(none)'} does not match {rule.mime_type}",
            )
    if size <= 0:
        raise MediaValidationError("MEDIA_EMPTY", "file is empty")
    if size > rule.max_bytes:
        raise MediaValidationError(
            "MEDIA_TOO_LARGE", f"{rule.kind} files are limited to {rule.max_bytes // KB} KB"
        )
    if not _signature_ok(rule, head):
        raise MediaValidationError(
            "MEDIA_CONTENT_MISMATCH", f"file content does not look like {rule.mime_type}"
        )
    return rule
