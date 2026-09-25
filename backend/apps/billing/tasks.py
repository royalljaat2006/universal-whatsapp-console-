from celery import shared_task

from apps.billing.services import price_message, price_pending_messages


@shared_task
def calculate_message_cost(message_id: str) -> str:
    return price_message(message_id)


@shared_task
def price_pending_message_costs() -> dict[str, int]:
    return price_pending_messages()
