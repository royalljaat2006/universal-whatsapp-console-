"""Transactional email delivery. Routed through Celery (default queue, see
workers/queues.py) so a slow SMTP call never blocks the request/response
cycle — the same reasoning apps.notifications already follows. With the
default console EMAIL_BACKEND (see config/settings/base.py) these just log;
switching to a real provider needs no code change here.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _send(*, subject: str, template: str, context: dict, to: str) -> None:
    """Plain-text body (base Message) + an HTML alternative, from one pair of
    templates (emails/<template>.txt / .html) — so clients that don't render
    HTML still get a usable email, and (a lesser but real benefit) a
    text-only part reduces the chance of the message being flagged as spam."""
    text_body = render_to_string(f"emails/{template}.txt", context)
    html_body = render_to_string(f"emails/{template}.html", context)
    message = EmailMultiAlternatives(
        subject=subject, body=text_body, from_email=settings.DEFAULT_FROM_EMAIL, to=[to]
    )
    message.attach_alternative(html_body, "text/html")
    message.send()


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_verification_email(self, *, email: str, verify_url: str) -> None:
    try:
        _send(
            subject="Verify your email",
            template="verify_email",
            context={"verify_url": verify_url},
            to=email,
        )
    except Exception as exc:
        logger.warning("verification email send failed, retrying: %s", exc)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_password_reset_email(self, *, email: str, reset_url: str) -> None:
    try:
        _send(
            subject="Reset your password",
            template="password_reset",
            context={"reset_url": reset_url},
            to=email,
        )
    except Exception as exc:
        logger.warning("password reset email send failed, retrying: %s", exc)
        raise self.retry(exc=exc) from exc
