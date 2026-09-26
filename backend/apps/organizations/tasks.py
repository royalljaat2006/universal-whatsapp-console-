"""Transactional email delivery for organization membership (invitations).
Routed through Celery (default queue, see workers/queues.py) so a slow SMTP
call never blocks the request/response cycle. With the default console
EMAIL_BACKEND (see config/settings/base.py) these just log; switching to a
real provider needs no code change here — see apps.identity.tasks for the
same pattern used by verification/password-reset email.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_invitation_email(
    self, *, email: str, organization_name: str, inviter_name: str, role_name: str, accept_url: str
) -> None:
    try:
        context = {
            "organization_name": organization_name,
            "inviter_name": inviter_name,
            "role_name": role_name,
            "accept_url": accept_url,
        }
        text_body = render_to_string("emails/invitation.txt", context)
        html_body = render_to_string("emails/invitation.html", context)
        message = EmailMultiAlternatives(
            subject=f"You're invited to {organization_name}",
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        )
        message.attach_alternative(html_body, "text/html")
        message.send()
    except Exception as exc:
        logger.warning("invitation email send failed, retrying: %s", exc)
        raise self.retry(exc=exc) from exc
