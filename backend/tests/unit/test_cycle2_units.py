"""Cycle 2 pure logic: template parameters, media rules, send policy, Meta media ops."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from django.utils import timezone

from apps.media.rules import MediaValidationError, validate_media
from apps.messaging import policy
from apps.templates.parameters import (
    TemplateParameterError,
    UnsupportedTemplateError,
    build,
    describe,
)
from providers.whatsapp.errors import ProviderError, Retryability
from providers.whatsapp.meta.config import MetaConfig
from providers.whatsapp.meta.provider import MetaWhatsAppProvider
from providers.whatsapp.types import SenderRef
from tests.fakes import FakeGraph, bytes_response, graph_error, json_response

# ---------------------------------------------------------------------------
# Template parameters
# ---------------------------------------------------------------------------


def template(components: list[dict[str, Any]], fmt: str = "POSITIONAL") -> Any:
    return SimpleNamespace(components=components, parameter_format=fmt)


RICH = [
    {"type": "HEADER", "format": "IMAGE"},
    {"type": "BODY", "text": "Hi {{1}}, order {{2}} shipped ({{1}})."},
    {"type": "FOOTER", "text": "Reply STOP"},
    {
        "type": "BUTTONS",
        "buttons": [
            {"type": "URL", "text": "Track", "url": "https://x.example/t/{{1}}"},
            {"type": "QUICK_REPLY", "text": "Help"},
        ],
    },
]


class TestTemplateParameters:
    def test_describe_positional_media_header_and_url_button(self) -> None:
        shape = describe(template(RICH))  # type: ignore[arg-type]
        keys = [(p.component, p.key, p.kind) for p in shape.parameters]
        assert keys == [
            ("header", "media", "image"),
            ("body", "1", "text"),
            ("body", "2", "text"),
            ("button", "0", "text"),
        ]

    def test_build_positional(self) -> None:
        built = build(
            template(RICH),  # type: ignore[arg-type]
            {
                "header": {"media_asset_id": "A"},
                "body": {"1": "Rajesh", "2": "ORD-1"},
                "buttons": {"0": "abc"},
            },
        )
        assert built.components == [
            {"type": "header", "parameters": [{"type": "image", "image": {"asset_id": "A"}}]},
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Rajesh"},
                    {"type": "text", "text": "ORD-1"},
                ],
            },
            {
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [{"type": "text", "text": "abc"}],
            },
        ]
        assert built.rendered_body == "Hi Rajesh, order ORD-1 shipped (Rajesh)."
        assert built.header_media_asset_id == "A"

    def test_build_named_parameters_carry_parameter_name(self) -> None:
        named = template(
            [
                {"type": "HEADER", "format": "TEXT", "text": "Hello {{name}}"},
                {"type": "BODY", "text": "Your code is {{code}}."},
            ],
            fmt="NAMED",
        )
        built = build(named, {"header": {"text": "Ann"}, "body": {"code": "42"}})  # type: ignore[arg-type]
        assert built.components == [
            {
                "type": "header",
                "parameters": [{"type": "text", "text": "Ann", "parameter_name": "name"}],
            },
            {
                "type": "body",
                "parameters": [{"type": "text", "text": "42", "parameter_name": "code"}],
            },
        ]

    def test_missing_unknown_and_empty_values_are_reported_per_field(self) -> None:
        with pytest.raises(TemplateParameterError) as info:
            build(template(RICH), {"body": {"1": " ", "9": "x"}})  # type: ignore[arg-type]
        assert set(info.value.errors) == {
            "header.media_asset_id",
            "body.1",
            "body.2",
            "body.9",
            "buttons.0",
        }

    def test_template_without_parameters(self) -> None:
        built = build(template([{"type": "BODY", "text": "Hello!"}]), {})  # type: ignore[arg-type]
        assert built.components == [] and built.rendered_body == "Hello!"

    def test_carousel_is_explicitly_unsupported(self) -> None:
        with pytest.raises(UnsupportedTemplateError):
            describe(template([{"type": "BODY", "text": "x"}, {"type": "CAROUSEL", "cards": []}]))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Media rules
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8


class TestMediaRules:
    def test_valid_files(self) -> None:
        assert (
            validate_media(
                mime_type="image/png", filename="a.png", size=100, head=PNG, outbound=True
            ).kind
            == "image"
        )
        assert (
            validate_media(
                mime_type="video/mp4", filename="v.mp4", size=100, head=MP4, outbound=True
            ).kind
            == "video"
        )
        assert (
            validate_media(
                mime_type="application/pdf",
                filename="d.PDF",
                size=100,
                head=b"%PDF-1.4",
                outbound=True,
            ).kind
            == "document"
        )

    @pytest.mark.parametrize(
        ("kwargs", "code"),
        [
            (
                {"mime_type": "image/gif", "filename": "a.gif", "size": 10, "head": b"GIF89a"},
                "UNSUPPORTED_MEDIA_TYPE",
            ),
            (
                {"mime_type": "image/png", "filename": "a.jpg", "size": 10, "head": PNG},
                "MEDIA_EXTENSION_MISMATCH",
            ),
            (
                {
                    "mime_type": "image/png",
                    "filename": "a.png",
                    "size": 6 * 1024 * 1024,
                    "head": PNG,
                },
                "MEDIA_TOO_LARGE",
            ),
            (
                {"mime_type": "image/png", "filename": "a.png", "size": 10, "head": b"%PDF-1.4"},
                "MEDIA_CONTENT_MISMATCH",
            ),
            (
                {"mime_type": "image/png", "filename": "a.png", "size": 0, "head": b""},
                "MEDIA_EMPTY",
            ),
            (
                {
                    "mime_type": "image/webp",
                    "filename": "s.webp",
                    "size": 10,
                    "head": b"RIFF\x00\x00\x00\x00WEBP",
                },
                "UNSUPPORTED_MEDIA_TYPE",
            ),
        ],
    )
    def test_rejections(self, kwargs: dict[str, Any], code: str) -> None:
        with pytest.raises(MediaValidationError) as info:
            validate_media(outbound=True, **kwargs)
        assert info.value.code == code

    def test_inbound_sticker_is_accepted(self) -> None:
        rule = validate_media(
            mime_type="image/webp",
            filename="",
            size=10,
            head=b"RIFF\x00\x00\x00\x00WEBP",
            outbound=False,
        )
        assert rule.kind == "sticker"


# ---------------------------------------------------------------------------
# Send policy (customer service window)
# ---------------------------------------------------------------------------


class TestSendPolicy:
    def conversation(self, last_inbound: Any) -> Any:
        from apps.conversations.models import Conversation

        return Conversation(last_inbound_at=last_inbound)

    def test_no_customer_message_requires_template(self) -> None:
        decision = policy.evaluate(self.conversation(None))
        assert not decision.allows(policy.FREE_FORM) and decision.allows(policy.TEMPLATE)
        assert decision.reason == "TEMPLATE_REQUIRED_NO_CUSTOMER_MESSAGE"

    def test_open_window_allows_free_form(self) -> None:
        now = timezone.now()
        decision = policy.evaluate(
            self.conversation(now - timedelta(hours=23, minutes=59)), now=now
        )
        assert decision.allows(policy.FREE_FORM) and decision.window_open

    def test_closed_window_requires_template(self) -> None:
        now = timezone.now()
        decision = policy.evaluate(self.conversation(now - timedelta(hours=24)), now=now)
        assert not decision.allows(policy.FREE_FORM) and decision.allows(policy.TEMPLATE)
        assert decision.reason == "TEMPLATE_REQUIRED_WINDOW_CLOSED"


# ---------------------------------------------------------------------------
# Meta media operations (fake HTTP only)
# ---------------------------------------------------------------------------

SENDER = SenderRef(phone_number_external_id="222", access_token="EAAsecret")
CONFIG = MetaConfig("https://graph.facebook.com", "v26.0", 5, "secret", "verify")
MEDIA_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1"


def provider(graph: FakeGraph) -> MetaWhatsAppProvider:
    return MetaWhatsAppProvider(
        CONFIG, transport=graph.transport, media_hosts=["lookaside.fbsbx.com"]
    )


class TestMetaMedia:
    def test_upload_is_multipart_with_documented_fields(self, fake_graph: FakeGraph) -> None:
        fake_graph.on("POST", "/222/media", {"id": "MEDIA1"})
        media_id = provider(fake_graph).upload_media(
            SENDER, data=PNG, mime_type="image/png", filename="a.png"
        )
        assert media_id == "MEDIA1"
        request = fake_graph.calls[0]
        assert request.headers["Content-Type"].startswith("multipart/form-data")
        body = request.content
        assert b'name="messaging_product"' in body and b"whatsapp" in body
        assert b'name="type"' in body and b"image/png" in body
        assert b'name="file"; filename="a.png"' in body

    def test_get_media_passes_phone_number_id(self, fake_graph: FakeGraph) -> None:
        fake_graph.on(
            "GET",
            "/M1",
            {
                "url": MEDIA_URL,
                "mime_type": "image/jpeg",
                "sha256": "abc",
                "file_size": 10,
                "id": "M1",
            },
        )
        info = provider(fake_graph).get_media(SENDER, "M1")
        assert info.url == MEDIA_URL and info.file_size == 10
        assert fake_graph.calls[0].url.params["phone_number_id"] == "222"

    def test_download_sends_bearer_and_enforces_cap(self, fake_graph: FakeGraph) -> None:
        fake_graph.on(
            "GET",
            "lookaside.fbsbx.com/whatsapp_business/attachments/",
            bytes_response(200, b"x" * 50),
        )
        data = provider(fake_graph).download_media(SENDER, MEDIA_URL, max_bytes=100)
        assert data == b"x" * 50
        assert fake_graph.calls[0].headers["Authorization"] == "Bearer EAAsecret"
        with pytest.raises(ProviderError) as info:
            provider(fake_graph).download_media(SENDER, MEDIA_URL, max_bytes=10)
        assert info.value.code == "MEDIA_TOO_LARGE"

    def test_expired_url_is_a_safe_retry(self, fake_graph: FakeGraph) -> None:
        fake_graph.on(
            "GET", "lookaside.fbsbx.com/whatsapp_business/attachments/", bytes_response(404, b"")
        )
        with pytest.raises(ProviderError) as info:
            provider(fake_graph).download_media(SENDER, MEDIA_URL, max_bytes=100)
        assert (
            info.value.code == "MEDIA_URL_EXPIRED" and info.value.retryability == Retryability.SAFE
        )

    @pytest.mark.parametrize("url", ["https://evil.example/x", "http://lookaside.fbsbx.com/x"])
    def test_foreign_or_insecure_media_url_is_refused(
        self, fake_graph: FakeGraph, url: str
    ) -> None:
        with pytest.raises(ProviderError) as info:
            provider(fake_graph).download_media(SENDER, url, max_bytes=100)
        assert info.value.code == "MEDIA_URL_REJECTED"
        assert fake_graph.calls == []

    def test_unknown_media_id_is_permanent(self, fake_graph: FakeGraph) -> None:
        fake_graph.on("GET", "/GONE", graph_error(400, 100, "Unsupported get request"))
        with pytest.raises(ProviderError) as info:
            provider(fake_graph).get_media(SENDER, "GONE")
        assert info.value.retryability == Retryability.PERMANENT

    def test_download_connection_error_is_safe(self, fake_graph: FakeGraph) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        fake_graph.on("GET", "lookaside.fbsbx.com/whatsapp_business/attachments/", boom)
        with pytest.raises(ProviderError) as info:
            provider(fake_graph).download_media(SENDER, MEDIA_URL, max_bytes=100)
        assert info.value.retryability == Retryability.SAFE


def test_json_response_helper_still_available() -> None:
    assert json_response(200, {})(httpx.Request("GET", "https://x")).status_code == 200
