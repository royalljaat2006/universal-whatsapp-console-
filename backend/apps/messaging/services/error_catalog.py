"""User-safe explanations for provider error codes.

Codes and meanings verified against Meta's Cloud API error-code reference
(docs/integrations/meta/api-assumptions.md#error-codes). The provider's own
message is kept in ``error_details`` for operators; users see these hints.
"""

HINTS: dict[str, str] = {
    "131047": "More than 24 hours have passed since the customer last replied. Send an approved template instead.",
    "131026": "The message could not be delivered (the number may not be on WhatsApp or the app is outdated).",
    "131051": "This message type is not supported.",
    "131052": "WhatsApp could not download the customer's media. Ask them to resend it.",
    "131053": "WhatsApp could not process the uploaded media (unsupported type or size).",
    "131000": "WhatsApp reported a temporary error.",
    "131016": "WhatsApp is temporarily unavailable.",
    "131021": "The sender and recipient are the same number.",
    "131048": "The sending number is restricted because of spam reports. Check its quality in WhatsApp Manager.",
    "131056": "Too many messages to this customer in a short time.",
    "130429": "The number's sending throughput limit was reached.",
    "132000": "The template parameters do not match the template definition.",
    "132001": "The template does not exist in this language or is not approved.",
    "132005": "The translated template text is too long.",
    "132007": "The template content violates WhatsApp policy.",
    "132012": "A template parameter has the wrong format.",
    "132015": "The template is paused because of low quality.",
    "132016": "The template has been permanently disabled.",
    "190": "The WhatsApp access token has expired or is invalid. An administrator must update the credential.",
    "368": "The WhatsApp account is temporarily restricted for policy violations.",
}

INTERNAL_HINTS: dict[str, str] = {
    "PROVIDER_OUTCOME_UNKNOWN": "The send was interrupted; WhatsApp may or may not have delivered it. Check before resending.",
    "CONFIGURATION_REQUIRED": "WhatsApp credentials are not configured for this number.",
    "PROVIDER_AUTH_FAILED": "The WhatsApp access token was rejected. An administrator must update the credential.",
    "PROVIDER_RATE_LIMITED": "WhatsApp rate limit reached.",
    "MEDIA_STORAGE_FAILED": "The attachment could not be read from storage.",
}


def hint(provider_code: str | None, internal_code: str | None, fallback: str) -> str:
    if provider_code and provider_code in HINTS:
        return HINTS[provider_code]
    if internal_code and internal_code in INTERNAL_HINTS:
        return INTERNAL_HINTS[internal_code]
    return fallback
