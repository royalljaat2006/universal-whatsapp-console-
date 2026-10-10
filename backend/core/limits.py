"""Operational safeguards against unbounded per-organization growth.

These are reliability / abuse guards (an invitation is an outbound email, an
export is a heavy worker job, an API key is a long-lived credential), set high
enough that normal use never notices them and configurable per deployment
through settings. They are deliberately NOT commercial limits and do not
depend on any subscription plan: a value of 0 disables a guard.

The check-then-create is not serialised, so two simultaneous requests can
overshoot a cap by a small amount; that is acceptable for a safeguard whose
job is to stop runaway or hostile volume, not to meter usage exactly.
"""

from django.conf import settings
from rest_framework import status

from core.exceptions import DomainError


class OperationalLimitError(DomainError):
    code = "OPERATIONAL_LIMIT_REACHED"
    status_code = status.HTTP_409_CONFLICT
    default_message = "A safety limit for this organization has been reached."


def enforce_cap(*, setting: str, current: int, noun: str, hint: str) -> None:
    """Raise when ``current`` has reached the cap configured in ``settings.<setting>``."""
    cap = int(getattr(settings, setting, 0))
    if cap > 0 and current >= cap:
        raise OperationalLimitError(
            f"Limit reached: at most {cap} {noun} at a time. {hint}",
            details={"limit": cap, "setting": setting},
        )
