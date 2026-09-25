import httpx
import pytest

from providers.whatsapp.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    Retryability,
)
from providers.whatsapp.meta.config import MetaConfig
from providers.whatsapp.meta.payloads import build_send_payload
from providers.whatsapp.meta.provider import MetaWhatsAppProvider
from providers.whatsapp.types import (
    AccountRef,
    MediaContent,
    MediaKind,
    OutboundMessage,
    ProviderAcceptance,
    Recipient,
    SenderRef,
    TemplateContent,
    TextContent,
)
from tests.fakes import FakeGraph, graph_error, json_response, raise_exc

CONFIG = MetaConfig("https://graph.facebook.com", "v26.0", 5, "secret", "verify")
SENDER = SenderRef(phone_number_external_id="222", access_token="EAAsecret")
ACCOUNT = AccountRef(business_account_external_id="111", access_token="EAAsecret")


def provider_with(graph: FakeGraph) -> MetaWhatsAppProvider:
    return MetaWhatsAppProvider(CONFIG, transport=graph.transport)


class TestPayloads:
    def test_text_to_phone(self) -> None:
        payload = build_send_payload(
            OutboundMessage(Recipient(phone_number="919876543210"), TextContent("hi"))
        )
        assert payload == {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "919876543210",
            "type": "text",
            "text": {"body": "hi", "preview_url": False},
        }

    def test_bsuid_only_uses_recipient_field(self) -> None:
        payload = build_send_payload(OutboundMessage(Recipient(bsuid="US.123"), TextContent("hi")))
        assert payload["recipient"] == "US.123"
        assert "to" not in payload

    def test_template_with_components_and_reply_context(self) -> None:
        payload = build_send_payload(
            OutboundMessage(
                Recipient(phone_number="1"),
                TemplateContent("order_shipped_v1", "en", [{"type": "body", "parameters": []}]),
                reply_to_external_id="wamid.X",
            )
        )
        assert payload["template"] == {
            "name": "order_shipped_v1",
            "language": {"code": "en"},
            "components": [{"type": "body", "parameters": []}],
        }
        assert payload["context"] == {"message_id": "wamid.X"}

    def test_media_caption_dropped_for_audio(self) -> None:
        payload = build_send_payload(
            OutboundMessage(
                Recipient(phone_number="1"),
                MediaContent(MediaKind.AUDIO, media_id="m1", caption="ignored"),
            )
        )
        assert payload["audio"] == {"id": "m1"}

    def test_recipient_requires_identifier(self) -> None:
        with pytest.raises(ValueError):
            Recipient()


class TestSend:
    def test_success_parses_acceptance(self, fake_graph: FakeGraph) -> None:
        fake_graph.on(
            "POST",
            "/222/messages",
            {
                "messaging_product": "whatsapp",
                "contacts": [{"input": "919876543210", "wa_id": "919876543210"}],
                "messages": [{"id": "wamid.OK", "message_status": "accepted"}],
            },
        )
        result = provider_with(fake_graph).send_message(
            SENDER, OutboundMessage(Recipient(phone_number="919876543210"), TextContent("hi"))
        )
        assert result.external_message_id == "wamid.OK"
        assert result.acceptance == ProviderAcceptance.ACCEPTED
        request = fake_graph.calls[0]
        assert str(request.url) == "https://graph.facebook.com/v26.0/222/messages"
        assert request.headers["Authorization"] == "Bearer EAAsecret"

    @pytest.mark.parametrize(
        ("responder", "exc_type", "retryability"),
        [
            (graph_error(400, 130429), ProviderRateLimitError, Retryability.SAFE),
            (graph_error(400, 131056), ProviderRateLimitError, Retryability.SAFE),
            (graph_error(400, 80007), ProviderRateLimitError, Retryability.SAFE),
            (graph_error(401, 190), ProviderAuthError, Retryability.PERMANENT),
            (graph_error(400, 131047), ProviderError, Retryability.PERMANENT),
            (graph_error(500, 131016), ProviderError, Retryability.TRANSIENT),
            (graph_error(502, 1), ProviderError, Retryability.AMBIGUOUS),
            (raise_exc(httpx.ConnectError("refused")), ProviderError, Retryability.SAFE),
            (raise_exc(httpx.ReadTimeout("slow")), ProviderError, Retryability.AMBIGUOUS),
        ],
    )
    def test_error_classification(self, fake_graph, responder, exc_type, retryability) -> None:  # type: ignore[no-untyped-def]
        fake_graph.on("POST", "/222/messages", responder)
        with pytest.raises(exc_type) as info:
            provider_with(fake_graph).send_message(
                SENDER, OutboundMessage(Recipient(phone_number="1"), TextContent("hi"))
            )
        assert info.value.retryability == retryability

    def test_get_5xx_is_transient_not_ambiguous(self, fake_graph: FakeGraph) -> None:
        fake_graph.on("GET", "/222", graph_error(503, 1))
        with pytest.raises(ProviderError) as info:
            provider_with(fake_graph).get_phone_number(SENDER)
        assert info.value.retryability == Retryability.TRANSIENT

    def test_2xx_without_message_id_is_outcome_unknown(self, fake_graph: FakeGraph) -> None:
        fake_graph.on("POST", "/222/messages", {"messages": []})
        with pytest.raises(ProviderError) as info:
            provider_with(fake_graph).send_message(
                SENDER, OutboundMessage(Recipient(phone_number="1"), TextContent("hi"))
            )
        assert info.value.code == "PROVIDER_OUTCOME_UNKNOWN"


class TestAccountOperations:
    def test_list_phone_numbers_follows_same_host_paging(self, fake_graph: FakeGraph) -> None:
        page2 = "https://graph.facebook.com/v26.0/111/phone_numbers?after=CUR"
        fake_graph.on(
            "GET",
            "/111/phone_numbers",
            {"data": [{"id": "1", "display_phone_number": "+1 555"}], "paging": {"next": page2}},
            {
                "data": [
                    {
                        "id": "2",
                        "display_phone_number": "+1 556",
                        "throughput": {"level": "STANDARD"},
                    }
                ]
            },
        )
        numbers = provider_with(fake_graph).list_phone_numbers(ACCOUNT)
        assert [n.external_id for n in numbers] == ["1", "2"]
        assert numbers[1].throughput_level == "STANDARD"

    def test_foreign_paging_url_is_not_followed(self, fake_graph: FakeGraph) -> None:
        fake_graph.on(
            "GET",
            "/111/phone_numbers",
            {
                "data": [{"id": "1", "display_phone_number": "+1"}],
                "paging": {"next": "https://evil.example/steal"},
            },
        )
        numbers = provider_with(fake_graph).list_phone_numbers(ACCOUNT)
        assert len(numbers) == 1
        assert len(fake_graph.calls) == 1

    def test_subscribe_webhooks(self, fake_graph: FakeGraph) -> None:
        fake_graph.on("POST", "/111/subscribed_apps", {"success": True})
        provider_with(fake_graph).subscribe_webhooks(ACCOUNT)
        assert fake_graph.calls[0].method == "POST"

    def test_health_check_reports_failure_without_raising(self, fake_graph: FakeGraph) -> None:
        fake_graph.on("GET", "/222", graph_error(401, 190))
        status = provider_with(fake_graph).health_check(SENDER)
        assert status.ok is False
        assert status.detail == "PROVIDER_AUTH_FAILED"


def test_config_rejects_bad_version() -> None:
    with pytest.raises(ValueError):
        MetaConfig("https://graph.facebook.com", "26", 5, "", "")


def test_config_requires_https() -> None:
    with pytest.raises(ValueError):
        MetaConfig("http://graph.facebook.com", "v26.0", 5, "", "")


def test_unknown_http_status_codes_map_to_rejected(fake_graph: FakeGraph) -> None:
    fake_graph.on(
        "POST", "/222/messages", json_response(400, {"error": {"code": 100, "message": "bad"}})
    )
    with pytest.raises(ProviderError) as info:
        provider_with(fake_graph).send_message(
            SENDER, OutboundMessage(Recipient(phone_number="1"), TextContent("hi"))
        )
    assert info.value.code == "PROVIDER_REJECTED"
    assert info.value.provider_code == "100"


class TestBusinessAccount:
    def test_get_business_account_requests_verified_fields(self, fake_graph: FakeGraph) -> None:
        fake_graph.on(
            "GET",
            "/111",
            {"id": "111", "name": "Kiosk", "business_verification_status": "VERIFIED"},
        )
        info = provider_with(fake_graph).get_business_account(ACCOUNT)
        assert info.name == "Kiosk" and info.business_verification_status == "VERIFIED"
        fields = fake_graph.calls[0].url.params["fields"].split(",")
        assert fields == [
            "id",
            "name",
            "timezone_id",
            "message_template_namespace",
            "account_review_status",
            "business_verification_status",
            "country",
            "ownership_type",
        ]

    @pytest.mark.parametrize(("body", "expected"), [({"data": [{}]}, True), ({"data": []}, False)])
    def test_is_app_subscribed(self, fake_graph: FakeGraph, body: dict, expected: bool) -> None:  # type: ignore[type-arg]
        fake_graph.on("GET", "/111/subscribed_apps", body)
        assert provider_with(fake_graph).is_app_subscribed(ACCOUNT) is expected
