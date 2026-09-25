"""Provider error taxonomy.

The key distinction for outbound messaging is whether a failed call *might
already have been executed* by the provider. Meta's send API has no
idempotency key, so blindly retrying an ambiguous failure can deliver the
same message twice. Retry policy therefore keys off ``Retryability``.
"""

from enum import StrEnum


class Retryability(StrEnum):
    # Request certainly not executed (connection refused, throttled): safe to retry.
    SAFE = "safe"
    # Provider reported a transient failure after receiving the request, and
    # documents it as retryable (e.g. 131000/131016).
    TRANSIENT = "transient"
    # Outcome unknown (timeout after the request was sent): never auto-retry.
    AMBIGUOUS = "ambiguous"
    # Permanent for this request (bad params, policy, auth): do not retry.
    PERMANENT = "permanent"


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "PROVIDER_ERROR",
        retryability: Retryability = Retryability.PERMANENT,
        http_status: int | None = None,
        provider_code: str | None = None,
        retry_after_seconds: float | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryability = retryability
        self.http_status = http_status
        self.provider_code = provider_code
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}

    @property
    def is_retryable(self) -> bool:
        return self.retryability in (Retryability.SAFE, Retryability.TRANSIENT)


class ProviderAuthError(ProviderError):
    """Credential invalid/expired/insufficient — operator action required."""


class ProviderRateLimitError(ProviderError):
    """Provider-side throttling; retry after backing off."""


class ProviderCapabilityError(ProviderError):
    """Operation not supported by this provider/adapter."""


class WebhookVerificationError(Exception):
    """Inbound webhook failed authenticity checks."""
