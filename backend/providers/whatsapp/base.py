"""Stable internal contracts every WhatsApp provider implements.

Designed from verified Meta Cloud API capabilities (docs/integrations/meta/)
but expressed in provider-neutral terms. Sending is a single
``send_message(OutboundMessage)`` with a typed content union rather than one
method per message type: adding a type extends the union and the adapter's
payload builder without widening every provider's surface.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from typing import Any

from providers.whatsapp.types import (
    AccountRef,
    BusinessAccountInfo,
    BusinessProfileInfo,
    HealthStatus,
    MediaInfo,
    OutboundMessage,
    PhoneNumberInfo,
    ProviderEvent,
    SenderRef,
    SendResult,
    TemplateInfo,
    TemplateSpec,
)


class WhatsAppProvider(ABC):
    """Outbound operations against a provider API. Implementations must be
    stateless apart from configuration and safe to share across threads."""

    name: str

    # -- messaging ---------------------------------------------------------
    @abstractmethod
    def send_message(self, sender: SenderRef, message: OutboundMessage) -> SendResult: ...

    @abstractmethod
    def mark_message_read(self, sender: SenderRef, external_message_id: str) -> None: ...

    # -- media ---------------------------------------------------------------
    @abstractmethod
    def upload_media(self, sender: SenderRef, *, data: bytes, mime_type: str, filename: str) -> str:
        """Upload a file for sending from this number; returns the provider media id."""

    @abstractmethod
    def get_media(self, sender: SenderRef, media_id: str) -> MediaInfo:
        """Resolve a media id to a short-lived download URL and metadata."""

    @abstractmethod
    def download_media(self, sender: SenderRef, url: str, *, max_bytes: int) -> bytes:
        """Download media bytes from a URL returned by ``get_media``."""

    # -- numbers / profile -------------------------------------------------
    @abstractmethod
    def list_phone_numbers(self, account: AccountRef) -> list[PhoneNumberInfo]: ...

    @abstractmethod
    def get_phone_number(self, sender: SenderRef) -> PhoneNumberInfo: ...

    @abstractmethod
    def get_business_profile(self, sender: SenderRef) -> BusinessProfileInfo: ...

    # -- templates ---------------------------------------------------------
    @abstractmethod
    def list_templates(self, account: AccountRef) -> Iterator[TemplateInfo]: ...

    @abstractmethod
    def create_template(self, account: AccountRef, spec: TemplateSpec) -> TemplateInfo: ...

    @abstractmethod
    def update_template(
        self, account: AccountRef, template_external_id: str, components: list[dict[str, Any]]
    ) -> None: ...

    @abstractmethod
    def delete_template(
        self, account: AccountRef, name: str, template_external_id: str | None = None
    ) -> None: ...

    # -- account -------------------------------------------------------------
    @abstractmethod
    def get_business_account(self, account: AccountRef) -> BusinessAccountInfo:
        """Fetch account attributes; also serves as the connection check
        (valid credential + account reachable)."""

    @abstractmethod
    def is_app_subscribed(self, account: AccountRef) -> bool:
        """Whether this platform's app receives the account's webhooks."""

    @abstractmethod
    def subscribe_webhooks(self, account: AccountRef) -> None: ...

    @abstractmethod
    def health_check(self, sender: SenderRef) -> HealthStatus: ...


class WebhookAdapter(ABC):
    """Inbound side: authenticate and normalise provider webhook deliveries."""

    name: str

    @abstractmethod
    def verify_subscription(self, params: Mapping[str, str]) -> str | None:
        """Handle a subscription handshake; return the challenge to echo, or
        None if the handshake is invalid."""

    @abstractmethod
    def verify_signature(self, raw_body: bytes, headers: Mapping[str, str]) -> None:
        """Raise WebhookVerificationError unless the delivery is authentic."""

    @abstractmethod
    def parse(self, payload: Mapping[str, Any]) -> list[ProviderEvent]:
        """Normalise one delivery into provider-neutral events. Unknown
        shapes must be skipped (and logged), never raise for the batch."""

    @abstractmethod
    def routing_keys(self, payload: Mapping[str, Any]) -> dict[str, list[str]]:
        """Cheap extraction of account / number ids for indexing raw events."""
