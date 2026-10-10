"""Outbound email configuration: bounded SMTP timeout and the deployment checks."""

from typing import Any

from django.core.mail import get_connection

from core.checks import check_email_configuration

SMTP = "django.core.mail.backends.smtp.EmailBackend"


def _ids(settings: Any) -> set[str]:
    return {w.id for w in check_email_configuration(None)}


def test_smtp_connections_get_a_bounded_timeout_by_default() -> None:
    from django.conf import settings

    assert settings.EMAIL_TIMEOUT == 15
    connection = get_connection(SMTP)
    assert connection.timeout == 15  # type: ignore[attr-defined]


def test_good_configuration_raises_no_warnings(settings: Any) -> None:
    settings.EMAIL_BACKEND = SMTP
    settings.EMAIL_TIMEOUT = 15
    settings.PUBLIC_BASE_URL = "https://console.example.com/whatsapp-console"
    assert _ids(settings) == set()


def test_non_https_or_local_base_url_is_flagged(settings: Any) -> None:
    settings.EMAIL_BACKEND = SMTP
    for url in ("http://console.example.com", "https://localhost:8080", "http://127.0.0.1:8080"):
        settings.PUBLIC_BASE_URL = url
        assert "core.W001" in _ids(settings), url


def test_unbounded_smtp_timeout_is_flagged(settings: Any) -> None:
    settings.EMAIL_BACKEND = SMTP
    settings.PUBLIC_BASE_URL = "https://console.example.com"
    settings.EMAIL_TIMEOUT = 0
    assert _ids(settings) == {"core.W002"}


def test_console_backend_is_reported_as_not_real_delivery(settings: Any) -> None:
    settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    settings.PUBLIC_BASE_URL = "https://console.example.com"
    assert _ids(settings) == {"core.W003"}
