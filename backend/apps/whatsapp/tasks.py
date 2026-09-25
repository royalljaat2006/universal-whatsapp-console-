from typing import Any

from celery import shared_task

from apps.whatsapp.models import WhatsAppBusinessAccount
from apps.whatsapp.services import run_connection_pipeline, sync_phone_numbers, verify_connection
from providers.whatsapp.errors import ProviderError
from workers.retry import retry_or_raise


def _account(business_account_id: str) -> WhatsAppBusinessAccount:
    return WhatsAppBusinessAccount.objects.select_related("credential", "organization").get(
        pk=business_account_id
    )


@shared_task(bind=True, max_retries=5)
def connect_business_account(self: Any, business_account_id: str) -> str:
    """Verify -> subscribe webhooks -> sync numbers. Each step is idempotent,
    so a retry after a transient failure simply resumes."""
    try:
        run_connection_pipeline(_account(business_account_id))
    except ProviderError as exc:
        retry_or_raise(self, exc)
    return "connected"


@shared_task(bind=True, max_retries=5)
def verify_business_account(self: Any, business_account_id: str) -> str:
    try:
        return verify_connection(_account(business_account_id)).connection_state
    except ProviderError as exc:
        retry_or_raise(self, exc)


@shared_task(bind=True, max_retries=5)
def sync_business_account_numbers(self: Any, business_account_id: str) -> int:
    try:
        return len(sync_phone_numbers(_account(business_account_id)))
    except ProviderError as exc:
        retry_or_raise(self, exc)
