import json
import logging

import pytest

from core import crypto
from core.exceptions import ConfigurationRequiredError
from core.ids import uuid7
from core.logging import JsonFormatter, redact
from core.secrets import SecretReference, SecretReferenceError, resolve_reference


class TestUuid7:
    def test_version_and_variant(self) -> None:
        value = uuid7()
        assert value.version == 7
        assert value.variant == "specified in RFC 4122"

    def test_monotonic_within_process(self) -> None:
        values = [uuid7() for _ in range(5000)]
        assert values == sorted(values)
        assert len(set(values)) == len(values)


class TestSecretReferences:
    def test_env_reference_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROVIDER_SECRET_X", "value")
        assert resolve_reference("env:PROVIDER_SECRET_X") == "value"

    @pytest.mark.parametrize(
        "raw",
        ["env:DATABASE_URL", "env:DJANGO_SECRET_KEY", "vault:foo", "PROVIDER_SECRET_X", "env:"],
    )
    def test_rejects_unsafe_or_malformed(self, raw: str) -> None:
        with pytest.raises(SecretReferenceError):
            SecretReference.parse(raw)

    def test_missing_secret_is_configuration_required(self) -> None:
        with pytest.raises(ConfigurationRequiredError):
            resolve_reference("env:PROVIDER_SECRET_DOES_NOT_EXIST")


class TestCrypto:
    def test_roundtrip_and_rotation(self, settings) -> None:  # type: ignore[no-untyped-def]
        crypto.reset_key_cache()
        token = crypto.encrypt("EAAtoken")
        assert token != b"EAAtoken"
        assert crypto.decrypt(token) == "EAAtoken"

        from cryptography.fernet import Fernet

        new_key = Fernet.generate_key().decode()
        settings.FIELD_ENCRYPTION_KEYS = [new_key, *settings.FIELD_ENCRYPTION_KEYS]
        crypto.reset_key_cache()
        assert crypto.decrypt(token) == "EAAtoken"  # old key still decrypts
        rotated = crypto.rotate(token)
        settings.FIELD_ENCRYPTION_KEYS = [new_key]
        crypto.reset_key_cache()
        assert crypto.decrypt(rotated) == "EAAtoken"
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(token)
        crypto.reset_key_cache()


class TestRedaction:
    def test_redacts_sensitive_keys_recursively(self) -> None:
        data = {"access_token": "EAAxxx", "nested": {"Authorization": "Bearer abc"}, "ok": 1}
        assert redact(data) == {
            "access_token": "[REDACTED]",
            "nested": {"Authorization": "[REDACTED]"},
            "ok": 1,
        }

    def test_redacts_tokens_inside_free_text(self) -> None:
        text = "call failed with Bearer abc.def and EAABwzLixnjYBO1234567890abcdefgh"
        assert "abc.def" not in redact(text)
        assert "EAABwz" not in redact(text)

    def test_json_formatter_never_emits_secrets(self) -> None:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "token=%s", ("x",), None)
        record.app_secret = "should-not-appear"
        line = json.loads(JsonFormatter().format(record))
        assert line["app_secret"] == "[REDACTED]"
