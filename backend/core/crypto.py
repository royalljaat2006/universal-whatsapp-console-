"""Symmetric encryption for secrets stored in the database.

Uses MultiFernet over FIELD_ENCRYPTION_KEYS: the first key encrypts, every
key decrypts. Rotation = prepend a new key, run ``rotate`` over stored
values, then drop the old key.
"""

from functools import lru_cache

from cryptography.fernet import Fernet as _Fernet
from cryptography.fernet import InvalidToken, MultiFernet
from django.conf import settings

from core.exceptions import ConfigurationRequiredError


class DecryptionError(Exception):
    pass


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    keys = [k.strip() for k in settings.FIELD_ENCRYPTION_KEYS if k.strip()]
    if not keys:
        raise ConfigurationRequiredError(
            "FIELD_ENCRYPTION_KEYS is not configured; encrypted credentials are unavailable."
        )
    return MultiFernet([_Fernet(k.encode()) for k in keys])


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(ciphertext)).decode()
    except InvalidToken as exc:
        raise DecryptionError("ciphertext could not be decrypted with configured keys") from exc


def rotate(ciphertext: bytes) -> bytes:
    """Re-encrypt with the current primary key."""
    return _fernet().rotate(bytes(ciphertext))


def reset_key_cache() -> None:
    _fernet.cache_clear()
