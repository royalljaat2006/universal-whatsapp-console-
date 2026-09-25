"""Mapping of Meta Graph/Cloud API errors onto the provider error taxonomy.

Codes verified against the official error-code reference (see
docs/integrations/meta/api-assumptions.md#error-codes). Unknown codes default
to PERMANENT — the safe choice for a non-idempotent send API.
"""

from typing import Any

from providers.whatsapp.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    Retryability,
)

# Throttling: the request was rejected, not executed -> safe to retry later.
THROTTLING_CODES = frozenset({4, 80007, 130429, 131056, 133016})
# Token/permission problems -> operator action required.
AUTH_CODES = frozenset({0, 3, 10, 190}) | frozenset(range(200, 300))
# Documented by Meta as transient/retryable message-send failures.
TRANSIENT_CODES = frozenset({131000, 131016})


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def error_from_response(
    http_status: int, body: dict[str, Any] | None, *, idempotent: bool
) -> ProviderError:
    error = (body or {}).get("error") or {}
    code = _as_int(error.get("code"))
    message = str(error.get("message") or f"Meta API returned HTTP {http_status}")
    details: dict[str, object] = {
        "type": error.get("type"),
        "error_subcode": error.get("error_subcode"),
        "fbtrace_id": error.get("fbtrace_id"),
        "error_data": error.get("error_data"),
    }
    provider_code = str(code) if code is not None else None
    clean_details = {k: v for k, v in details.items() if v is not None}

    def make(
        cls: type[ProviderError], error_code: str, retryability: Retryability
    ) -> ProviderError:
        return cls(
            message,
            code=error_code,
            retryability=retryability,
            http_status=http_status,
            provider_code=provider_code,
            details=clean_details,
        )

    if http_status == 429 or (code is not None and code in THROTTLING_CODES):
        return make(ProviderRateLimitError, "PROVIDER_RATE_LIMITED", Retryability.SAFE)
    if http_status in (401, 403) or (code is not None and code in AUTH_CODES):
        return make(ProviderAuthError, "PROVIDER_AUTH_FAILED", Retryability.PERMANENT)
    if code is not None and code in TRANSIENT_CODES:
        return make(ProviderError, "PROVIDER_TRANSIENT", Retryability.TRANSIENT)
    if http_status >= 500:
        # A 5xx without a recognised code may or may not have been executed.
        retryability = Retryability.TRANSIENT if idempotent else Retryability.AMBIGUOUS
        return make(ProviderError, "PROVIDER_UNAVAILABLE", retryability)
    return make(ProviderError, "PROVIDER_REJECTED", Retryability.PERMANENT)
