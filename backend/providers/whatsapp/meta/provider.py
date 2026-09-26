"""MetaWhatsAppProvider — WhatsAppProvider implementation for the Cloud API."""

import time
from collections.abc import Iterator
from typing import Any

import httpx

from providers.whatsapp.base import WhatsAppProvider
from providers.whatsapp.errors import ProviderError
from providers.whatsapp.meta.client import GraphClient
from providers.whatsapp.meta.config import MetaConfig
from providers.whatsapp.meta.payloads import build_mark_read_payload, build_send_payload
from providers.whatsapp.types import (
    AccountRef,
    BusinessAccountInfo,
    BusinessProfileInfo,
    HealthStatus,
    MediaInfo,
    OutboundMessage,
    PhoneNumberInfo,
    ProviderAcceptance,
    SenderRef,
    SendResult,
    TemplateInfo,
    TemplateSpec,
)

# Field lists verified against the phone number / business profile / template
# references (docs/integrations/meta/api-assumptions.md). Requesting an unknown
# field fails the whole call, so only documented fields are listed.
PHONE_NUMBER_FIELDS = (
    "id,display_phone_number,verified_name,status,quality_rating,"
    "code_verification_status,name_status,messaging_limit_tier,throughput"
)
# Verified queryable fields of GET /{WABA_ID} (2026-09-23).
BUSINESS_ACCOUNT_FIELDS = (
    "id,name,timezone_id,message_template_namespace,account_review_status,"
    "business_verification_status,country,ownership_type"
)
BUSINESS_PROFILE_FIELDS = "about,address,description,email,profile_picture_url,websites,vertical"
TEMPLATE_FIELDS = (
    "id,name,language,category,status,components,parameter_format,quality_score,rejected_reason"
)


def _str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        # e.g. throughput {"level": "STANDARD"} / quality_score {"score": "GREEN"}
        return str(value.get("level") or value.get("score") or "")
    return str(value)


def _phone_info(data: dict[str, Any]) -> PhoneNumberInfo:
    return PhoneNumberInfo(
        external_id=str(data["id"]),
        display_phone_number=_str(data.get("display_phone_number")),
        verified_name=_str(data.get("verified_name")),
        quality_rating=_str(data.get("quality_rating")),
        status=_str(data.get("status")),
        name_status=_str(data.get("name_status")),
        code_verification_status=_str(data.get("code_verification_status")),
        messaging_limit_tier=_str(data.get("messaging_limit_tier")),
        throughput_level=_str(data.get("throughput")),
        raw=data,
    )


def _template_info(data: dict[str, Any]) -> TemplateInfo:
    return TemplateInfo(
        external_id=str(data["id"]),
        name=_str(data.get("name")),
        language=_str(data.get("language")),
        category=_str(data.get("category")),
        status=_str(data.get("status")),
        components=list(data.get("components") or []),
        parameter_format=_str(data.get("parameter_format")),
        quality_score=_str(data.get("quality_score")),
        rejected_reason=_str(data.get("rejected_reason")),
        raw=data,
    )


class MetaWhatsAppProvider(WhatsAppProvider):
    name = "meta"

    def __init__(
        self,
        config: MetaConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        media_hosts: list[str] | None = None,
    ):
        self._config = config
        self._client = GraphClient(config, transport=transport)
        self._media_hosts = list(media_hosts or [])

    # -- messaging ---------------------------------------------------------
    def send_message(self, sender: SenderRef, message: OutboundMessage) -> SendResult:
        body = self._client.request(
            "POST",
            f"/{sender.phone_number_external_id}/messages",
            token=sender.access_token,
            json=build_send_payload(message),
        )
        messages = body.get("messages") or []
        if not messages or not messages[0].get("id"):
            # HTTP 2xx without a message id: Meta may or may not have queued it.
            raise ProviderError(
                "Meta accepted the request but returned no message id",
                code="PROVIDER_OUTCOME_UNKNOWN",
            )
        first = messages[0]
        try:
            acceptance = ProviderAcceptance(first.get("message_status") or "accepted")
        except ValueError:
            acceptance = ProviderAcceptance.UNKNOWN
        contacts = body.get("contacts") or [{}]
        return SendResult(
            external_message_id=str(first["id"]),
            acceptance=acceptance,
            recipient_wa_id=contacts[0].get("wa_id"),
            raw=body,
        )

    def mark_message_read(self, sender: SenderRef, external_message_id: str) -> None:
        self._client.request(
            "POST",
            f"/{sender.phone_number_external_id}/messages",
            token=sender.access_token,
            json=build_mark_read_payload(external_message_id),
        )

    # -- media ---------------------------------------------------------------
    def upload_media(self, sender: SenderRef, *, data: bytes, mime_type: str, filename: str) -> str:
        # Verified: POST /{PHONE_NUMBER_ID}/media, multipart fields
        # messaging_product, type (MIME), file -> {"id": "<MEDIA_ID>"}.
        body = self._client.request(
            "POST",
            f"/{sender.phone_number_external_id}/media",
            token=sender.access_token,
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": (filename or "file", data, mime_type)},
        )
        media_id = body.get("id")
        if not media_id:
            raise ProviderError("Meta returned no media id", code="PROVIDER_OUTCOME_UNKNOWN")
        return str(media_id)

    def get_media(self, sender: SenderRef, media_id: str) -> MediaInfo:
        # Verified: GET /{MEDIA_ID}?phone_number_id=... -> url (valid 5 min),
        # mime_type, sha256, file_size, id.
        body = self._client.request(
            "GET",
            f"/{media_id}",
            token=sender.access_token,
            params={"phone_number_id": sender.phone_number_external_id},
        )
        if not body.get("url"):
            raise ProviderError("Meta returned no media URL", code="MEDIA_URL_MISSING")
        size = body.get("file_size")
        return MediaInfo(
            media_id=str(body.get("id") or media_id),
            url=str(body["url"]),
            mime_type=_str(body.get("mime_type")),
            sha256=_str(body.get("sha256")),
            file_size=int(str(size)) if size not in (None, "") else None,
        )

    def download_media(self, sender: SenderRef, url: str, *, max_bytes: int) -> bytes:
        return self._client.download(
            url, token=sender.access_token, allowed_hosts=self._media_hosts, max_bytes=max_bytes
        )

    # -- numbers / profile -------------------------------------------------
    def list_phone_numbers(self, account: AccountRef) -> list[PhoneNumberInfo]:
        return [
            _phone_info(item)
            for item in self._client.iter_pages(
                f"/{account.business_account_external_id}/phone_numbers",
                token=account.access_token,
                params={"fields": PHONE_NUMBER_FIELDS},
            )
        ]

    def get_phone_number(self, sender: SenderRef) -> PhoneNumberInfo:
        data = self._client.request(
            "GET",
            f"/{sender.phone_number_external_id}",
            token=sender.access_token,
            params={"fields": PHONE_NUMBER_FIELDS},
        )
        return _phone_info(data)

    def get_business_profile(self, sender: SenderRef) -> BusinessProfileInfo:
        body = self._client.request(
            "GET",
            f"/{sender.phone_number_external_id}/whatsapp_business_profile",
            token=sender.access_token,
            params={"fields": BUSINESS_PROFILE_FIELDS},
        )
        data = (body.get("data") or [{}])[0]
        return BusinessProfileInfo(
            about=_str(data.get("about")),
            address=_str(data.get("address")),
            description=_str(data.get("description")),
            email=_str(data.get("email")),
            vertical=_str(data.get("vertical")),
            websites=tuple(data.get("websites") or ()),
            raw=data,
        )

    # -- templates ---------------------------------------------------------
    def list_templates(self, account: AccountRef) -> Iterator[TemplateInfo]:
        for item in self._client.iter_pages(
            f"/{account.business_account_external_id}/message_templates",
            token=account.access_token,
            params={"fields": TEMPLATE_FIELDS, "limit": 100},
        ):
            yield _template_info(item)

    def create_template(self, account: AccountRef, spec: TemplateSpec) -> TemplateInfo:
        payload: dict[str, Any] = {
            "name": spec.name,
            "language": spec.language,
            "category": spec.category,
            "components": spec.components,
        }
        if spec.parameter_format:
            payload["parameter_format"] = spec.parameter_format
        body = self._client.request(
            "POST",
            f"/{account.business_account_external_id}/message_templates",
            token=account.access_token,
            json=payload,
        )
        return TemplateInfo(
            external_id=str(body["id"]),
            name=spec.name,
            language=spec.language,
            category=_str(body.get("category")) or spec.category,
            status=_str(body.get("status")),
            components=spec.components,
            parameter_format=spec.parameter_format or "",
            raw=body,
        )

    def update_template(
        self, account: AccountRef, template_external_id: str, components: list[dict[str, Any]]
    ) -> None:
        self._client.request(
            "POST",
            f"/{template_external_id}",
            token=account.access_token,
            json={"components": components},
        )

    def delete_template(
        self, account: AccountRef, name: str, template_external_id: str | None = None
    ) -> None:
        params = {"name": name}
        if template_external_id:
            params["hsm_id"] = template_external_id
        self._client.request(
            "DELETE",
            f"/{account.business_account_external_id}/message_templates",
            token=account.access_token,
            params=params,
        )

    # -- account -------------------------------------------------------------
    def get_business_account(self, account: AccountRef) -> BusinessAccountInfo:
        data = self._client.request(
            "GET",
            f"/{account.business_account_external_id}",
            token=account.access_token,
            params={"fields": BUSINESS_ACCOUNT_FIELDS},
        )
        return BusinessAccountInfo(
            external_id=str(data.get("id") or account.business_account_external_id),
            name=_str(data.get("name")),
            timezone_id=_str(data.get("timezone_id")),
            message_template_namespace=_str(data.get("message_template_namespace")),
            account_review_status=_str(data.get("account_review_status")),
            business_verification_status=_str(data.get("business_verification_status")),
            country=_str(data.get("country")),
            ownership_type=_str(data.get("ownership_type")),
            raw=data,
        )

    def is_app_subscribed(self, account: AccountRef) -> bool:
        # GET /{WABA_ID}/subscribed_apps lists the apps receiving this WABA's
        # webhooks. Only the presence of entries is relied upon; the entry
        # shape is not (see docs/integrations/meta/api-assumptions.md).
        body = self._client.request(
            "GET",
            f"/{account.business_account_external_id}/subscribed_apps",
            token=account.access_token,
        )
        return bool(body.get("data"))

    def subscribe_webhooks(self, account: AccountRef) -> None:
        self._client.request(
            "POST",
            f"/{account.business_account_external_id}/subscribed_apps",
            token=account.access_token,
        )

    # -- onboarding (Embedded Signup) -----------------------------------------
    def exchange_code_for_token(self, code: str) -> str:
        # Verified 2026-09-26 (docs/integrations/meta/api-assumptions.md):
        # GET /{version}/oauth/access_token?client_id=&client_secret=&code=
        # -> {"access_token": "<business token>"}. No bearer token yet — that's
        # the point of this call — so `token` is intentionally omitted.
        body = self._client.request(
            "GET",
            "/oauth/access_token",
            params={
                "client_id": self._config.app_id,
                "client_secret": self._config.app_secret,
                "code": code,
            },
        )
        token = body.get("access_token")
        if not token:
            raise ProviderError(
                "Meta accepted the request but returned no access token",
                code="PROVIDER_OUTCOME_UNKNOWN",
            )
        return str(token)

    def register_phone_number(self, sender: SenderRef, *, pin: str) -> None:
        # Verified 2026-09-26: POST /{PHONE_NUMBER_ID}/register
        # {"messaging_product": "whatsapp", "pin": "<6 digits>"}.
        self._client.request(
            "POST",
            f"/{sender.phone_number_external_id}/register",
            token=sender.access_token,
            json={"messaging_product": "whatsapp", "pin": pin},
        )

    def health_check(self, sender: SenderRef) -> HealthStatus:
        started = time.perf_counter()
        try:
            self._client.request(
                "GET",
                f"/{sender.phone_number_external_id}",
                token=sender.access_token,
                params={"fields": "id"},
            )
        except ProviderError as exc:
            return HealthStatus(
                ok=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                detail=exc.code,
            )
        return HealthStatus(ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 2))
