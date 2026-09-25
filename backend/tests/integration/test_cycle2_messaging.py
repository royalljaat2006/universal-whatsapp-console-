"""Cycle 2 messaging against real PostgreSQL + fake Graph API:
template sends, media upload/send, inbound media, window policy."""

import json
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.media.models import MediaAsset
from apps.messaging.models import Message, MessageAttachment
from apps.messaging.services.media_download import download_inbound_attachment
from apps.messaging.services.outbound import dispatch_outbound_message
from apps.messaging.state import MessageStatus
from tests import factories
from tests.conftest import api_client_for, load_fixture
from tests.fakes import FakeGraph, bytes_response, graph_error

pytestmark = pytest.mark.django_db(transaction=True)

TO = "919876543210"
ACCEPTED = {
    "messaging_product": "whatsapp",
    "contacts": [{"input": TO, "wa_id": TO}],
    "messages": [{"id": "wamid.T1", "message_status": "accepted"}],
}
MEDIA_URL_PATH = "lookaside.fbsbx.com/whatsapp_business/attachments/"
MEDIA_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=9"


def _template_payload(phone: Any, template: Any, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "phone_number_id": str(phone.pk),
        "to": TO,
        "type": "template",
        "template": {"template_id": str(template.pk), "parameters": parameters},
    }


class TestTemplateSending:
    def test_rich_template_is_built_server_side_and_header_media_uploaded(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        template = factories.make_rich_template(phone.business_account)
        image = factories.make_media_asset(phone.business)
        fake_graph.on("POST", f"/{phone.external_id}/media", {"id": "PROVIDER_IMG"})
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)

        response = owner_api.post(
            "/api/v1/messages/",
            _template_payload(
                phone,
                template,
                {
                    "header": {"media_asset_id": str(image.pk)},
                    "body": {"1": "Rajesh", "2": "ORD-88213"},
                    "buttons": {"0": "88213"},
                },
            ),
            format="json",
        )
        assert response.status_code == 202, response.content
        body = response.json()
        assert body["text"] == "Hi Rajesh, order ORD-88213 has shipped."
        assert body["template"]["parameters"]["body"] == {"1": "Rajesh", "2": "ORD-88213"}

        [sent] = fake_graph.json_bodies("POST", "/messages")
        assert sent["type"] == "template"
        assert sent["template"]["name"] == "order_shipped_v1"
        header, body_component, button = sent["template"]["components"]
        assert header == {
            "type": "header",
            "parameters": [{"type": "image", "image": {"id": "PROVIDER_IMG"}}],
        }
        assert [p["text"] for p in body_component["parameters"]] == ["Rajesh", "ORD-88213"]
        assert button["sub_type"] == "url" and button["index"] == "0"
        message = Message.objects.get()
        assert message.status == MessageStatus.ACCEPTED
        assert message.template_id == template.pk
        header_attachment = message.attachments.get(role="header")
        assert header_attachment.provider_media_id == "PROVIDER_IMG"

    def test_parameter_errors_are_field_level_422(self, owner_api: Any, phone: Any) -> None:
        template = factories.make_rich_template(phone.business_account)
        response = owner_api.post(
            "/api/v1/messages/",
            _template_payload(phone, template, {"body": {"1": "x"}}),
            format="json",
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "TEMPLATE_PARAMETERS_INVALID"
        assert set(error["details"]["fields"]) == {"header.media_asset_id", "body.2", "buttons.0"}
        assert Message.objects.count() == 0

    def test_template_of_another_waba_is_not_sendable(
        self, owner_api: Any, organization: Any, phone: Any
    ) -> None:
        other_waba = factories.make_waba(organization, business=phone.business)
        foreign = factories.make_template(other_waba)
        response = owner_api.post(
            "/api/v1/messages/", _template_payload(phone, foreign, {"body": {}}), format="json"
        )
        assert response.json()["error"]["code"] == "TEMPLATE_NOT_SENDABLE"

    def test_template_deleted_at_provider_is_not_sendable(self, owner_api: Any, phone: Any) -> None:
        template = factories.make_template(phone.business_account)
        template.missing_from_provider = True
        template.save()
        response = owner_api.post(
            "/api/v1/messages/", _template_payload(phone, template, {}), format="json"
        )
        assert response.json()["error"]["code"] == "TEMPLATE_NOT_SENDABLE"

    def test_header_media_from_another_business_is_rejected(
        self, owner_api: Any, organization: Any, phone: Any
    ) -> None:
        template = factories.make_rich_template(phone.business_account)
        other_business = factories.make_business(organization, "EPS")
        foreign_image = factories.make_media_asset(other_business)
        response = owner_api.post(
            "/api/v1/messages/",
            _template_payload(
                phone,
                template,
                {
                    "header": {"media_asset_id": str(foreign_image.pk)},
                    "body": {"1": "a", "2": "b"},
                    "buttons": {"0": "c"},
                },
            ),
            format="json",
        )
        assert response.status_code == 404


class TestWindowPolicy:
    def test_free_form_outside_window_is_blocked_with_policy_details(
        self, owner_api: Any, phone: Any
    ) -> None:
        response = owner_api.post(
            "/api/v1/messages/",
            {"phone_number_id": str(phone.pk), "to": TO, "type": "text", "text": {"body": "hi"}},
            format="json",
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "SERVICE_WINDOW_CLOSED"
        assert error["details"]["policy"]["template_allowed"] is True
        assert error["details"]["policy"]["reason"] == "TEMPLATE_REQUIRED_NO_CUSTOMER_MESSAGE"

    def test_template_allowed_outside_window(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        template = factories.make_template(phone.business_account)
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        response = owner_api.post(
            "/api/v1/messages/",
            _template_payload(phone, template, {"body": {"1": "A"}}),
            format="json",
        )
        assert response.status_code == 202

    def test_conversation_exposes_policy(self, owner_api: Any, phone: Any) -> None:
        factories.open_service_window(phone, TO)
        conversation = phone.conversations.get()
        body = owner_api.get(f"/api/v1/conversations/{conversation.pk}/").json()
        assert body["send_policy"]["free_form_allowed"] is True
        assert body["send_policy"]["reason"] == "SERVICE_WINDOW_OPEN"


class TestMediaUploadAndSend:
    def _upload(self, client: Any, business: Any, name: str, data: bytes, mime: str) -> Any:
        return client.post(
            "/api/v1/media/",
            {"file": SimpleUploadedFile(name, data, content_type=mime)},
            format="multipart",
            HTTP_X_BUSINESS_ID=str(business.pk),
        )

    def test_upload_validates_and_stores_privately(
        self, owner_api: Any, phone: Any, media_storage: Any
    ) -> None:
        ok = self._upload(owner_api, phone.business, "a.png", factories.PNG_BYTES, "image/png")
        assert ok.status_code == 201, ok.content
        asset = MediaAsset.objects.get(pk=ok.json()["id"])
        assert asset.business_id == phone.business_id and asset.kind == "image"
        assert (media_storage / asset.storage_key).exists()
        bad = self._upload(
            owner_api, phone.business, "a.png", b"%PDF-1.4 not an image", "image/png"
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["details"]["reason"] == "MEDIA_CONTENT_MISMATCH"
        missing_business = owner_api.post(
            "/api/v1/media/",
            {"file": SimpleUploadedFile("a.png", factories.PNG_BYTES, content_type="image/png")},
            format="multipart",
        )
        assert missing_business.json()["error"]["code"] == "BUSINESS_REQUIRED"

    def test_content_is_served_only_within_business(
        self, owner: Any, organization: Any, phone: Any
    ) -> None:
        asset = factories.make_media_asset(phone.business)
        eps = factories.make_business(organization, "EPS")
        eps_agent = factories.make_member(organization, "agent", businesses=[eps])
        owner_client = api_client_for(owner, organization)
        response = owner_client.get(f"/api/v1/media/{asset.pk}/content/")
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == factories.PNG_BYTES
        assert response["Content-Security-Policy"].startswith("sandbox")
        denied = api_client_for(eps_agent, organization).get(f"/api/v1/media/{asset.pk}/content/")
        assert denied.status_code == 404

    def test_media_message_uploads_then_sends_by_id(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        factories.open_service_window(phone, TO)
        document = factories.make_media_asset(
            phone.business, kind="document", data=factories.PDF_BYTES
        )
        fake_graph.on("POST", f"/{phone.external_id}/media", {"id": "PROVIDER_DOC"})
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        response = owner_api.post(
            "/api/v1/messages/",
            {
                "conversation_id": str(phone.conversations.get().pk),
                "type": "media",
                "media": {
                    "media_asset_id": str(document.pk),
                    "caption": "Invoice",
                    "filename": "inv.pdf",
                },
            },
            format="json",
        )
        assert response.status_code == 202, response.content
        [sent] = fake_graph.json_bodies("POST", "/messages")
        assert sent["type"] == "document"
        assert sent["document"] == {
            "id": "PROVIDER_DOC",
            "caption": "Invoice",
            "filename": "inv.pdf",
        }
        assert Message.objects.get().attachments.get().provider_media_id == "PROVIDER_DOC"

    def test_media_upload_failure_is_retried_not_sent(
        self, owner_api: Any, phone: Any, fake_graph: FakeGraph, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr("apps.messaging.services.outbound.enqueue_dispatch", lambda _id: None)
        factories.open_service_window(phone, TO)
        image = factories.make_media_asset(phone.business)
        fake_graph.on("POST", f"/{phone.external_id}/media", graph_error(503, 1), {"id": "IMG2"})
        fake_graph.on("POST", f"/{phone.external_id}/messages", ACCEPTED)
        owner_api.post(
            "/api/v1/messages/",
            {
                "conversation_id": str(phone.conversations.get().pk),
                "type": "media",
                "media": {"media_asset_id": str(image.pk)},
            },
            format="json",
        )
        message = Message.objects.get()
        first = dispatch_outbound_message(message.pk)
        assert first.retry_in is not None  # upload is safe to repeat
        assert fake_graph.json_bodies("POST", "/messages") == []
        assert dispatch_outbound_message(message.pk).status == MessageStatus.ACCEPTED

    def test_media_of_other_business_cannot_be_sent(
        self, owner_api: Any, organization: Any, phone: Any
    ) -> None:
        factories.open_service_window(phone, TO)
        foreign = factories.make_media_asset(factories.make_business(organization, "EPS"))
        response = owner_api.post(
            "/api/v1/messages/",
            {
                "conversation_id": str(phone.conversations.get().pk),
                "type": "media",
                "media": {"media_asset_id": str(foreign.pk)},
            },
            format="json",
        )
        assert response.status_code == 404


class TestInboundMedia:
    def _deliver_image(self, post_webhook: Any, phone: Any, wamid: str = "wamid.IMG") -> None:
        post_webhook(
            load_fixture(
                "inbound_bsuid_only.json",
                WABA_ID=phone.business_account.external_id,
                PHONE_NUMBER_ID=phone.external_id,
                WAMID=wamid,
                TS="1780000000",
            )
        )

    def test_media_is_downloaded_async_and_stored(
        self, post_webhook: Any, phone: Any, fake_graph: FakeGraph, media_storage: Any
    ) -> None:
        png = factories.PNG_BYTES
        fake_graph.on(
            "GET", "/1234567890", {"url": MEDIA_URL, "mime_type": "image/png", "id": "1234567890"}
        )
        fake_graph.on("GET", MEDIA_URL_PATH, bytes_response(200, png, "image/png"))
        self._deliver_image(post_webhook, phone)
        attachment = MessageAttachment.objects.get()
        assert attachment.storage_status == MessageAttachment.StorageStatus.STORED
        assert attachment.media_asset.business_id == phone.business_id
        assert (media_storage / attachment.media_asset.storage_key).read_bytes() == png
        # duplicate webhook delivery: no second download
        calls = len(fake_graph.calls)
        payload = load_fixture(
            "inbound_bsuid_only.json",
            WABA_ID=phone.business_account.external_id,
            PHONE_NUMBER_ID=phone.external_id,
            WAMID="wamid.IMG",
            TS="1780000000",
        )
        payload["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"] = "X"
        post_webhook(payload)
        assert len(fake_graph.calls) == calls
        assert MessageAttachment.objects.count() == 1

    def test_expired_url_then_success_on_retry(
        self, post_webhook: Any, phone: Any, fake_graph: FakeGraph, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            "apps.messaging.services.media_download.enqueue_download", lambda _id: None
        )
        monkeypatch.setattr("apps.messaging.services.inbound.enqueue_download", lambda _id: None)
        fake_graph.on("GET", "/1234567890", {"url": MEDIA_URL, "mime_type": "image/png"})
        fake_graph.on(
            "GET",
            MEDIA_URL_PATH,
            bytes_response(404, b""),
            bytes_response(200, factories.PNG_BYTES, "image/png"),
        )
        self._deliver_image(post_webhook, phone)
        attachment = MessageAttachment.objects.get()
        first = download_inbound_attachment(attachment.pk)
        assert first.status == "retry"
        attachment.refresh_from_db()
        assert attachment.last_error.startswith("MEDIA_URL_EXPIRED")
        assert download_inbound_attachment(attachment.pk).status == "stored"
        # a fresh URL was requested for the second attempt
        assert [c.url.path for c in fake_graph.calls if c.url.host == "graph.facebook.com"] == [
            "/v26.0/1234567890",
            "/v26.0/1234567890",
        ]

    def test_expired_media_id_fails_permanently(
        self, post_webhook: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        fake_graph.on("GET", "/1234567890", graph_error(400, 100, "Unsupported get request"))
        self._deliver_image(post_webhook, phone)
        attachment = MessageAttachment.objects.get()
        assert attachment.storage_status == MessageAttachment.StorageStatus.FAILED
        assert attachment.last_error.startswith("MEDIA_UNAVAILABLE")
        # the message itself is still delivered to the inbox
        assert Message.objects.get().message_type == "image"

    def test_unsupported_type_fails_cleanly(
        self, post_webhook: Any, phone: Any, fake_graph: FakeGraph
    ) -> None:
        fake_graph.on("GET", "/1234567890", {"url": MEDIA_URL, "mime_type": "image/gif"})
        self._deliver_image(post_webhook, phone)
        assert MessageAttachment.objects.get().last_error.startswith("UNSUPPORTED_MEDIA_TYPE")

    def test_storage_failure_is_retried(
        self, post_webhook: Any, phone: Any, fake_graph: FakeGraph, monkeypatch: Any
    ) -> None:
        from apps.media.storage import MediaStorageError

        monkeypatch.setattr("apps.messaging.services.inbound.enqueue_download", lambda _id: None)
        fake_graph.on("GET", "/1234567890", {"url": MEDIA_URL, "mime_type": "image/png"})
        fake_graph.on("GET", MEDIA_URL_PATH, bytes_response(200, factories.PNG_BYTES, "image/png"))
        self._deliver_image(post_webhook, phone)

        def disk_full(**kwargs: Any) -> None:
            raise MediaStorageError("No space left on device")

        monkeypatch.setattr("apps.messaging.services.media_download.store_inbound", disk_full)
        outcome = download_inbound_attachment(MessageAttachment.objects.get().pk)
        assert outcome.status == "retry"
        assert (
            json.dumps(MessageAttachment.objects.get().last_error).find("MEDIA_STORAGE_FAILED") > 0
        )
