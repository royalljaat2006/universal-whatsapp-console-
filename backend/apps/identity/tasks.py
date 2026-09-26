"""Transactional email delivery. Routed through Celery (default queue, see
workers/queues.py) so a slow SMTP call never blocks the request/response
cycle — the same reasoning apps.notifications already follows. With the
default console EMAIL_BACKEND (see config/settings/base.py) these just log;
switching to a real provider needs no code change here.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_verification_email(self, *, email: str, verify_url: str) -> None:
    try:
        send_mail(
            subject="Verify your email",
            message=(
                f"Confirm this is your email address to finish setting up your account.\n\n"
                f"{verify_url}\n\n"
                f"This link doesn't expire, but stops working once verified. If you didn't "
                f"create this account, you can ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
        )
    except Exception as exc:
        logger.warning("verification email send failed, retrying: %s", exc)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_password_reset_email(self, *, email: str, reset_url: str) -> None:
    try:
        send_mail(
            subject="Reset your password",
            message=(
                f"Use this link to choose a new password:\n\n"
                f"{reset_url}\n\n"
                f"This link expires soon and works once. If you didn't request this, "
                f"you can ignore this email — your password hasn't changed."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
        )
    except Exception as exc:
        logger.warning("password reset email send failed, retrying: %s", exc)
        raise self.retry(exc=exc) from exc
