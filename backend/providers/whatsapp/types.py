"""Provider-neutral value types exchanged between domain services and providers."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SenderRef:
    """Which business number sends, and with which credential."""

    phone_number_external_id: str
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class AccountRef:
    business_account_external_id: str
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class Recipient:
    """A WhatsApp user. At least one identifier is required. When both are
    present the phone number takes precedence at Meta (verified behaviour)."""

    phone_number: str | None = None  # digits only, with country code
    bsuid: str | None = None  # business-scoped user id, e.g. "US.1349..."

    def __post_init__(self) -> None:
        if not (self.phone_number or self.bsuid):
            raise ValueError("recipient requires a phone number or a BSUID")


# ---------------------------------------------------------------------------
# Outbound content
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextContent:
    body: str
    preview_url: bool = False


@dataclass(frozen=True)
class TemplateContent:
    name: str
    language: str
    # Provider-shaped component parameters. Validation against the stored
    # template definition happens in the templates domain, not here.
    components: list[dict[str, Any]] = field(default_factory=list)


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"


@dataclass(frozen=True)
class MediaContent:
    kind: MediaKind
    media_id: str | None = None  # previously uploaded to the provider
    link: str | None = None  # public HTTPS URL
    caption: str | None = None
    filename: str | None = None

    def __post_init__(self) -> None:
        if bool(self.media_id) == bool(self.link):
            raise ValueError("media requires exactly one of media_id or link")


@dataclass(frozen=True)
class ReactionContent:
    message_external_id: str
    emoji: str  # empty string removes the reaction


OutboundContent = TextContent | TemplateContent | MediaContent | ReactionContent


@dataclass(frozen=True)
class OutboundMessage:
    recipient: Recipient
    content: OutboundContent
    reply_to_external_id: str | None = None


class ProviderAcceptance(StrEnum):
    """Provider's synchronous verdict on a send request (not delivery)."""

    ACCEPTED = "accepted"
    HELD_FOR_QUALITY_ASSESSMENT = "held_for_quality_assessment"
    PAUSED = "paused"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SendResult:
    external_message_id: str
    acceptance: ProviderAcceptance
    recipient_wa_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Account / number / template information
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhoneNumberInfo:
    external_id: str
    display_phone_number: str
    verified_name: str = ""
    quality_rating: str = ""
    status: str = ""
    name_status: str = ""
    code_verification_status: str = ""
    messaging_limit_tier: str = ""
    throughput_level: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class MediaInfo:
    """Result of GET /{MEDIA_ID}: a short-lived download URL (5 minutes at Meta)."""

    media_id: str
    url: str
    mime_type: str = ""
    sha256: str = ""
    file_size: int | None = None


@dataclass(frozen=True)
class BusinessAccountInfo:
    external_id: str
    name: str = ""
    timezone_id: str = ""
    message_template_namespace: str = ""
    account_review_status: str = ""
    business_verification_status: str = ""
    country: str = ""
    ownership_type: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class BusinessProfileInfo:
    about: str = ""
    address: str = ""
    description: str = ""
    email: str = ""
    vertical: str = ""
    websites: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class TemplateInfo:
    external_id: str
    name: str
    language: str
    category: str
    status: str
    components: list[dict[str, Any]] = field(default_factory=list)
    parameter_format: str = ""
    quality_score: str = ""
    rejected_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    language: str
    category: str
    components: list[dict[str, Any]]
    parameter_format: str | None = None


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    latency_ms: float
    detail: str = ""


# ---------------------------------------------------------------------------
# Normalised inbound events (output of a provider's webhook adapter)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContactProfile:
    phone_number: str | None
    bsuid: str | None
    parent_bsuid: str | None = None
    name: str = ""


@dataclass(frozen=True)
class InboundMessageEvent:
    phone_number_external_id: str
    business_account_external_id: str
    external_message_id: str
    sender: ContactProfile
    message_type: str
    occurred_at: datetime
    content: dict[str, Any]
    text: str = ""
    reply_to_external_id: str | None = None
    media: dict[str, Any] | None = None
    # The provider's original message object, stored verbatim on the Message.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ProviderErrorDetail:
    code: str
    title: str = ""
    message: str = ""
    details: str = ""


@dataclass(frozen=True)
class StatusUpdateEvent:
    phone_number_external_id: str
    business_account_external_id: str
    external_message_id: str
    status: str  # provider-native value, mapped by the domain
    occurred_at: datetime
    recipient: ContactProfile
    errors: tuple[ProviderErrorDetail, ...] = ()
    pricing: dict[str, Any] | None = None
    conversation: dict[str, Any] | None = None


@dataclass(frozen=True)
class TemplateStatusEvent:
    business_account_external_id: str
    template_external_id: str
    template_name: str
    language: str
    event: str
    reason: str = ""
    category: str = ""
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class AccountEvent:
    """Any other WABA-level notification (quality, limits, account updates).
    Routed and stored; specific handlers are added per field as needed."""

    business_account_external_id: str
    field_name: str
    value: dict[str, Any]
    occurred_at: datetime | None = None


ProviderEvent = InboundMessageEvent | StatusUpdateEvent | TemplateStatusEvent | AccountEvent
