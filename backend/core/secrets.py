"""Secret references.

Domain rows never hold raw secrets in plain columns. They hold a *reference*
resolved at the moment of use:

- ``env:PROVIDER_SECRET_X``  — process environment (populated by the
  deployment's secret store: Docker secrets, Vault agent, cloud SM, ...).
- ``encrypted``              — Fernet ciphertext stored alongside the row.

Adding a backend (e.g. ``vault:path#key`` or ``awssm:arn``) means adding a
resolver here; no domain code changes.
"""

import os
from dataclasses import dataclass

from django.conf import settings

from core.crypto import decrypt
from core.exceptions import ConfigurationRequiredError


class SecretReferenceError(ValueError):
    pass


@dataclass(frozen=True)
class SecretReference:
    backend: str
    locator: str

    @classmethod
    def parse(cls, raw: str) -> "SecretReference":
        backend, sep, locator = raw.partition(":")
        if not sep or not locator:
            raise SecretReferenceError("secret reference must look like '<backend>:<locator>'")
        if backend != "env":
            raise SecretReferenceError(f"unsupported secret backend '{backend}'")
        prefix = settings.PROVIDER_SECRET_ENV_PREFIX
        if not locator.startswith(prefix) or not locator.replace("_", "").isalnum():
            # Prevents a reference from reading unrelated process secrets
            # such as DATABASE_URL or DJANGO_SECRET_KEY.
            raise SecretReferenceError(f"env secret references must start with '{prefix}'")
        return cls(backend=backend, locator=locator)

    def __str__(self) -> str:
        return f"{self.backend}:{self.locator}"


def resolve_reference(raw: str) -> str:
    ref = SecretReference.parse(raw)
    value = os.environ.get(ref.locator, "").strip()
    if not value:
        raise ConfigurationRequiredError(f"secret '{ref}' is not set in this environment")
    return value


def resolve_encrypted(ciphertext: bytes | None) -> str:
    if not ciphertext:
        raise ConfigurationRequiredError("encrypted secret is empty")
    return decrypt(ciphertext)
