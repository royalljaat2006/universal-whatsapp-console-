"""Shared retry policy for tasks that call providers."""

import random
from typing import Any, NoReturn

from providers.whatsapp.errors import ProviderError

BASE_DELAY_SECONDS = 15
MAX_DELAY_SECONDS = 900


def backoff_seconds(attempt: int, *, hint: float | None = None) -> int:
    """Exponential backoff with full jitter; honours a provider hint if larger."""
    ceiling = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * (2**attempt))
    delay = random.uniform(BASE_DELAY_SECONDS / 2, ceiling)  # noqa: S311 - jitter, not crypto
    return int(max(delay, hint or 0))


def retry_or_raise(task: Any, exc: ProviderError) -> NoReturn:
    """Retry only provider errors classified as retryable; permanent and
    ambiguous failures propagate immediately."""
    if exc.is_retryable and task.request.retries < task.max_retries:
        raise task.retry(
            exc=exc,
            countdown=backoff_seconds(task.request.retries, hint=exc.retry_after_seconds),
        )
    raise exc
