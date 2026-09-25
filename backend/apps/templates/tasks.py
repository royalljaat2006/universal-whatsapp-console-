from typing import Any

from celery import shared_task

from apps.templates.services import sync_templates
from apps.whatsapp.models import WhatsAppBusinessAccount
from providers.whatsapp.errors import ProviderError
from workers.retry import retry_or_raise


@shared_task(bind=True, max_retries=5)
def sync_business_account_templates(self: Any, business_account_id: str) -> int:
    account = WhatsAppBusinessAccount.objects.select_related("credential").get(
        pk=business_account_id
    )
    try:
        return sync_templates(account)
    except ProviderError as exc:
        retry_or_raise(self, exc)
