from rest_framework import status

from core.exceptions import ConflictError, DomainError


class UnroutableEventError(Exception):
    """A provider event references an account/number we do not manage."""


class StatusTargetNotFoundError(Exception):
    """A status report references a message id we have not (yet) stored.

    Usually a race: the webhook arrived before the send transaction committed
    the provider id. The processor retries for a bounded window."""


class IdempotencyKeyReusedError(ConflictError):
    code = "IDEMPOTENCY_KEY_REUSED"
    default_message = "This Idempotency-Key was already used with a different request."


class PhoneNumberNotReadyError(DomainError):
    code = "PHONE_NUMBER_NOT_READY"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_message = "The sending phone number is not connected."


class ServiceWindowClosedError(DomainError):
    """Raised by the send policy (apps/messaging/policy.py)."""

    code = "SERVICE_WINDOW_CLOSED"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_message = (
        "Free-form messages require an open customer service window "
        "(customer replied within 24 hours). Send an approved template instead."
    )


class TemplateNotSendableError(DomainError):
    code = "TEMPLATE_NOT_SENDABLE"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_message = "The template does not exist for this account or is not approved."


class TemplateParametersInvalidError(DomainError):
    code = "TEMPLATE_PARAMETERS_INVALID"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_message = "The template parameters are invalid."


class MediaNotSendableError(DomainError):
    code = "MEDIA_NOT_SENDABLE"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_message = "The attachment cannot be sent."
