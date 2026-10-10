"""Deployment checks for outbound-email configuration (``manage.py check --deploy``).

Warnings, not errors: a hard startup failure over a link setting could
crash-loop a deployment that is otherwise healthy. They surface in
``check --deploy`` and in the release checklist, where they belong.
"""

from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Tags, Warning, register

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}  # noqa: S104 -- a denylist, not a bind


@register(Tags.security, deploy=True)
def check_email_configuration(app_configs: Any, **kwargs: Any) -> list[Warning]:
    problems: list[Warning] = []
    parsed = urlparse(settings.PUBLIC_BASE_URL)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host in _LOCAL_HOSTS or host.endswith(".local"):
        problems.append(
            Warning(
                "PUBLIC_BASE_URL is not a public HTTPS address; verification, password-reset "
                "and invitation emails will contain links recipients cannot use.",
                hint="Set PUBLIC_BASE_URL to the externally reachable https:// URL "
                "(including any path prefix).",
                id="core.W001",
            )
        )
    uses_smtp = settings.EMAIL_BACKEND.endswith("smtp.EmailBackend")
    if uses_smtp and not settings.EMAIL_TIMEOUT:
        problems.append(
            Warning(
                "EMAIL_TIMEOUT is unset or 0 while using SMTP; a stalled connection can block "
                "a worker task indefinitely.",
                hint="Set EMAIL_TIMEOUT to a small number of seconds (default 15).",
                id="core.W002",
            )
        )
    if not uses_smtp:
        problems.append(
            Warning(
                f"EMAIL_BACKEND is {settings.EMAIL_BACKEND!r}, not SMTP: emails are not "
                "delivered to real recipients.",
                hint="Use django.core.mail.backends.smtp.EmailBackend with the EMAIL_HOST_* "
                "settings for real delivery.",
                id="core.W003",
            )
        )
    return problems
