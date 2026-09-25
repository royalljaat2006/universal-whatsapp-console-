"""Group message failures by cause, and decide whether a retry is *safe*.

Inputs are what the platform stores: ``Message.error_code`` is either a Meta
error code reported by a status webhook (e.g. "131026") or an internal code
from the send pipeline (e.g. "PROVIDER_RATE_LIMITED"), with the Meta code in
``error_details.provider_code``. Codes are the verified ones listed in
docs/integrations/meta/api-assumptions.md#error-codes.

A retry is safe only when WhatsApp certainly did **not** send the message
and the cause can be temporary. ``PROVIDER_OUTCOME_UNKNOWN`` (timeout after
the request left) is never retried automatically: Meta has no idempotency
key, so a retry could deliver twice.
"""

from dataclasses import dataclass
from typing import Any

INVALID_RECIPIENT = "invalid_recipient"
PROVIDER_REJECTION = "provider_rejection"
RATE_LIMIT = "rate_limit"
AUTHENTICATION = "authentication"
TEMPLATE_ERROR = "template_error"
POLICY_WINDOW = "policy_window"
TIMEOUT = "timeout"
TEMPORARY = "temporary_provider_error"
CANCELLED = "cancelled"
UNKNOWN = "unknown"

LABELS: dict[str, str] = {
    INVALID_RECIPIENT: "Invalid recipient",
    PROVIDER_REJECTION: "Rejected by WhatsApp",
    RATE_LIMIT: "Rate limit",
    AUTHENTICATION: "Authentication / configuration",
    TEMPLATE_ERROR: "Template error",
    POLICY_WINDOW: "Policy / customer service window",
    TIMEOUT: "Timeout (outcome unknown)",
    TEMPORARY: "Temporary WhatsApp error",
    CANCELLED: "Cancelled before sending",
    UNKNOWN: "Unknown error",
}

RETRYABLE = frozenset({RATE_LIMIT, AUTHENTICATION, TEMPORARY})

_PROVIDER_CODES: dict[str, str] = {
    # recipient
    "131026": INVALID_RECIPIENT,
    "131021": INVALID_RECIPIENT,
    "131030": INVALID_RECIPIENT,
    # throttling
    "4": RATE_LIMIT,
    "80007": RATE_LIMIT,
    "130429": RATE_LIMIT,
    "131056": RATE_LIMIT,
    "133016": RATE_LIMIT,
    # auth
    "0": AUTHENTICATION,
    "3": AUTHENTICATION,
    "10": AUTHENTICATION,
    "190": AUTHENTICATION,
    # temporary
    "131000": TEMPORARY,
    "131016": TEMPORARY,
    # policy / window / account health
    "131047": POLICY_WINDOW,
    "131048": POLICY_WINDOW,
    "131049": POLICY_WINDOW,
    "368": POLICY_WINDOW,
}

_INTERNAL_CODES: dict[str, str] = {
    "PROVIDER_RATE_LIMITED": RATE_LIMIT,
    "PROVIDER_AUTH_FAILED": AUTHENTICATION,
    "CONFIGURATION_REQUIRED": AUTHENTICATION,
    "PROVIDER_TRANSIENT": TEMPORARY,
    "PROVIDER_UNAVAILABLE": TEMPORARY,
    "PROVIDER_OUTCOME_UNKNOWN": TIMEOUT,
    "PROVIDER_REJECTED": PROVIDER_REJECTION,
    "TEMPLATE_NOT_SENDABLE": TEMPLATE_ERROR,
    "TEMPLATE_PARAMETERS_INVALID": TEMPLATE_ERROR,
    "VARIABLE_MISSING": TEMPLATE_ERROR,
    "SERVICE_WINDOW_CLOSED": POLICY_WINDOW,
    "RECIPIENT_REQUIRED": INVALID_RECIPIENT,
    "INVALID_RECIPIENT": INVALID_RECIPIENT,
    "VALIDATION_ERROR": INVALID_RECIPIENT,
    "CAMPAIGN_CANCELLED": CANCELLED,
}


def _provider_category(code: str) -> str | None:
    if code in _PROVIDER_CODES:
        return _PROVIDER_CODES[code]
    if code.isdigit():
        number = int(code)
        if 200 <= number < 300:
            return AUTHENTICATION
        if 132000 <= number < 133000:
            return TEMPLATE_ERROR
        return PROVIDER_REJECTION  # any other documented Meta request error
    return None


def categorize(error_code: str | None, error_details: dict[str, Any] | None = None) -> str:
    code = (error_code or "").strip()
    provider_code = str((error_details or {}).get("provider_code") or "").strip()
    if provider_code:
        by_provider = _provider_category(provider_code)
        if by_provider and code not in {"PROVIDER_OUTCOME_UNKNOWN"}:
            return by_provider
    if code in _INTERNAL_CODES:
        return _INTERNAL_CODES[code]
    by_code = _provider_category(code) if code else None
    return by_code or UNKNOWN


@dataclass(frozen=True)
class FailureInfo:
    category: str
    label: str
    retryable: bool


def describe(error_code: str | None, error_details: dict[str, Any] | None = None) -> FailureInfo:
    category = categorize(error_code, error_details)
    return FailureInfo(category, LABELS[category], category in RETRYABLE)
