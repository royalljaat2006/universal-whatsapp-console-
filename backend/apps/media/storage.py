"""Media storage abstraction over Django's Storage API.

Development uses FileSystemStorage on a private volume (MEDIA_ROOT, never
served by nginx). Production swaps in an S3-compatible backend by changing
``STORAGES["media"]`` — no code change (see docs/media-architecture.md).
Keys are opaque and namespaced by tenant and business.
"""

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO

from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage, storages

from core.ids import uuid7


class MediaStorageError(Exception):
    """The storage backend failed (disk full, bucket unreachable, ...)."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str


def _storage() -> Storage:
    return storages["media"]


def build_key(organization_id: object, business_id: object, extension: str) -> str:
    now = datetime.now(tz=UTC)
    return f"org/{organization_id}/biz/{business_id}/{now:%Y/%m}/{uuid7().hex}{extension}"


def save(key: str, source: IO[bytes] | bytes) -> StoredObject:
    """Store bytes (or a file object) under ``key`` and return size + SHA-256."""
    digest = hashlib.sha256()
    if isinstance(source, bytes):
        digest.update(source)
        content: File = ContentFile(source)
        size = len(source)
    else:
        size = 0
        source.seek(0)
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        source.seek(0)
        content = File(source)
    try:
        stored_key = _storage().save(key, content)
    except OSError as exc:  # filesystem / network storage failure
        raise MediaStorageError(str(exc)) from exc
    return StoredObject(key=stored_key, size=size, sha256=digest.hexdigest())


def open_file(key: str) -> IO[bytes]:
    try:
        return _storage().open(key, "rb")
    except OSError as exc:
        raise MediaStorageError(str(exc)) from exc


def read_bytes(key: str) -> bytes:
    with open_file(key) as handle:
        return handle.read()


def iter_chunks(key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    with open_file(key) as handle:
        yield from iter(lambda: handle.read(chunk_size), b"")


def delete(key: str) -> None:
    try:
        _storage().delete(key)
    except OSError as exc:
        raise MediaStorageError(str(exc)) from exc
