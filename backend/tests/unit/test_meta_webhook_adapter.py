import hashlib
import hmac

import pytest

from providers.whatsapp.errors import WebhookVerificationError
from providers.whatsapp.meta.config import MetaConfig
from providers.whatsapp.meta.webhooks import MetaWebhookAdapter
from providers.whatsapp.types import (
    AccountEvent,
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusEvent,
)
from tests.conftest import load_fixture

CONFIG = MetaConfig(
    graph_base_url="https://graph.facebook.com",
    graph_version="v26.0",
    timeout_seconds=5,
    app_secret="s3cret",
    webhook_verify_token="verify-me",
)
adapter = MetaWebhookAdapter(CONFIG)
IDS = {"WABA_ID": "111", "PHONE_NUMBER_ID": "222", "WAMID": "wamid.ABC", "TS": "1749416383"}


class TestHandshake:
    def test_valid_handshake_returns_challenge(self) -> None:
        params = {
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "1158201444",
        }
        assert adapter.verify_subscription(params) == "1158201444"

    @pytest.mark.parametrize(
        "params",
        [
            {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
            {"hub.mode": "unsubscribe", "hub.verify_token": "verify-me", "hub.challenge": "1"},
            {"hub.mode": "subscribe", "hub.verify_token": "verify-me"},
        ],
    )
    def test_invalid_handshake_rejected(self, params: dict[str, str]) -> None:
        assert adapter.verify_subscription(params) is None

    def test_handshake_rejected_when_token_not_configured(self) -> None:
        unconfigured = MetaWebhookAdapter(
            MetaConfig("https://graph.facebook.com", "v26.0", 5, "s", webhook_verify_token="")
        )
        assert (
            unconfigured.verify_subscription(
                {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}
            )
            is None
        )


class TestSignature:
    body = b'{"object":"whatsapp_business_account"}'

    def _sig(self, secret: str = "s3cret") -> str:
        return "sha256=" + hmac.new(secret.encode(), self.body, hashlib.sha256).hexdigest()

    def test_valid_signature(self) -> None:
        adapter.verify_signature(self.body, {"X-Hub-Signature-256": self._sig()})

    @pytest.mark.parametrize(
        "header",
        ["", "sha1=abc", "sha256=deadbeef"],
    )
    def test_invalid_signature(self, header: str) -> None:
        with pytest.raises(WebhookVerificationError):
            adapter.verify_signature(self.body, {"X-Hub-Signature-256": header})

    def test_signature_with_other_secret_rejected(self) -> None:
        with pytest.raises(WebhookVerificationError):
            adapter.verify_signature(self.body, {"X-Hub-Signature-256": self._sig("other")})

    def test_tampered_body_rejected(self) -> None:
        with pytest.raises(WebhookVerificationError):
            adapter.verify_signature(self.body + b" ", {"X-Hub-Signature-256": self._sig()})


class TestParse:
    def test_inbound_text(self) -> None:
        [event] = adapter.parse(load_fixture("inbound_text.json", **IDS))
        assert isinstance(event, InboundMessageEvent)
        assert event.phone_number_external_id == "222"
        assert event.business_account_external_id == "111"
        assert event.external_message_id == "wamid.ABC"
        assert event.sender.phone_number == "16505551234"
        assert event.sender.bsuid == "US.13491208655302741918"
        assert event.sender.name == "Sheena Nelson"
        assert event.text == "Does it come in another color?"
        assert event.occurred_at.timestamp() == 1749416383

    def test_inbound_bsuid_only_media(self) -> None:
        [event] = adapter.parse(load_fixture("inbound_bsuid_only.json", **IDS))
        assert isinstance(event, InboundMessageEvent)
        assert event.sender.phone_number is None
        assert event.sender.bsuid == "US.99991208655302741918"
        assert event.message_type == "image"
        assert event.media and event.media["id"] == "1234567890"
        assert event.text == "photo"

    def test_status_with_pricing(self) -> None:
        [event] = adapter.parse(load_fixture("status.json", STATUS="delivered", **IDS))
        assert isinstance(event, StatusUpdateEvent)
        assert event.status == "delivered"
        assert event.pricing == {
            "billable": True,
            "pricing_model": "PMP",
            "type": "regular",
            "category": "utility",
        }
        assert event.recipient.bsuid == "US.13491208655302741918"

    def test_failed_status_errors(self) -> None:
        [event] = adapter.parse(load_fixture("status_failed.json", **IDS))
        assert isinstance(event, StatusUpdateEvent)
        assert event.errors[0].code == "131026"
        assert event.errors[0].details == "Message Undeliverable."

    def test_template_status(self) -> None:
        [event] = adapter.parse(
            load_fixture("template_status_approved.json", TEMPLATE_ID="1689556908129832", **IDS)
        )
        assert isinstance(event, TemplateStatusEvent)
        assert event.template_external_id == "1689556908129832"
        assert event.event == "APPROVED"
        assert event.category == "UTILITY"

    def test_unknown_field_becomes_account_event(self) -> None:
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "111",
                    "changes": [
                        {"field": "phone_number_quality_update", "value": {"event": "FLAGGED"}}
                    ],
                }
            ],
        }
        [event] = adapter.parse(payload)
        assert isinstance(event, AccountEvent)
        assert event.field_name == "phone_number_quality_update"

    def test_wrong_object_ignored(self) -> None:
        assert adapter.parse({"object": "page", "entry": []}) == []

    def test_malformed_change_does_not_drop_batch(self) -> None:
        good = load_fixture("inbound_text.json", **IDS)
        bad_change = {"field": "messages", "value": {"messages": [{"id": "x"}]}}  # no metadata
        good["entry"][0]["changes"].insert(0, bad_change)
        events = adapter.parse(good)
        assert len(events) == 1

    def test_routing_keys(self) -> None:
        keys = adapter.routing_keys(load_fixture("inbound_text.json", **IDS))
        assert keys == {
            "business_accounts": ["111"],
            "phone_numbers": ["222"],
            "fields": ["messages"],
        }
