"""Cycle 3 campaign engine against real PostgreSQL + fake Graph API.

Ticks are driven directly (CAMPAIGN_AUTO_TICK is off in tests); every send
goes through the real outbound pipeline, idempotency and status webhooks.
"""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.campaigns import engine
from apps.campaigns.models import Campaign, CampaignEvent, CampaignRecipient
from apps.messaging.models import Message
from apps.messaging.state import MessageStatus
from apps.realtime import publisher
from tests import factories
from tests.conftest import api_client_for, load_fixture
from tests.fakes import FakeGraph, accept_send, graph_error

pytestmark = pytest.mark.django_db(transaction=True)

R = CampaignRecipient.Status
S = Campaign.Status


@pytest.fixture
def world(owner: Any, organization: Any) -> dict[str, Any]:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")
    kiosk_waba = factories.make_waba(organization, business=kiosk)
    eps_waba = factories.make_waba(organization, business=eps)
    kiosk_phone = factories.make_phone_number(kiosk_waba)
    eps_phone = factories.make_phone_number(eps_waba)
    active = factories.make_tag(kiosk, "Active")
    eps_tag = factories.make_tag(eps, "Active")
    contacts = [
        factories.make_contact(kiosk, f"91990000{i:04d}", name=f"Customer {i}", tags=[active])
        for i in range(7)
    ]
    return {
        "kiosk": kiosk,
        "eps": eps,
        "kiosk_phone": kiosk_phone,
        "eps_phone": eps_phone,
        "kiosk_template": factories.make_campaign_template(kiosk_waba),
        "eps_template": factories.make_campaign_template(eps_waba),
        "active": active,
        "eps_tag": eps_tag,
        "contacts": contacts,
        "api": api_client_for(owner, organization, kiosk),
        "eps_api": api_client_for(owner, organization, eps),
    }


def _payload(world: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "September update",
        "phone_number_id": str(world["kiosk_phone"].pk),
        "template_id": str(world["kiosk_template"].pk),
        "audience_definition": {
            "match": "all",
            "conditions": [{"field": "tag", "op": "has", "value": str(world["active"].pk)}],
        },
        "variable_mapping": {"body": {"1": {"source": "contact.name"}}},
        "max_messages_per_minute": 60,
    }
    data.update(overrides)
    return data


def _create(world: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    response = world["api"].post("/api/v1/campaigns/", _payload(world, **overrides), format="json")
    assert response.status_code == 201, response.json()
    return response.json()


def _launch(world: dict[str, Any], campaign_id: str) -> Any:
    return world["api"].post(
        f"/api/v1/campaigns/{campaign_id}/launch/", {"confirm": True}, format="json"
    )


def _run_to_completion(campaign_id: str, max_ticks: int = 20) -> list[engine.TickResult]:
    results = []
    for _ in range(max_ticks):
        result = engine.tick(campaign_id)
        results.append(result)
        if result.next_in is None:
            break
    return results


class TestValidation:
    def test_foreign_or_invalid_references_are_rejected(self, world: dict[str, Any]) -> None:
        api = world["api"]
        eps_number = api.post(
            "/api/v1/campaigns/",
            _payload(world, phone_number_id=str(world["eps_phone"].pk)),
            format="json",
        )
        assert eps_number.status_code == 404
        eps_template = api.post(
            "/api/v1/campaigns/",
            _payload(world, template_id=str(world["eps_template"].pk)),
            format="json",
        )
        assert eps_template.status_code == 404
        pending = factories.make_campaign_template(
            world["kiosk_phone"].business_account, name="pending_one", status="PENDING"
        )
        unapproved = api.post(
            "/api/v1/campaigns/", _payload(world, template_id=str(pending.pk)), format="json"
        )
        assert unapproved.status_code == 422
        eps_tag = api.post(
            "/api/v1/campaigns/",
            _payload(
                world,
                audience_definition={
                    "conditions": [{"field": "tag", "op": "has", "value": str(world["eps_tag"].pk)}]
                },
            ),
            format="json",
        )
        assert eps_tag.status_code == 422
        assert eps_tag.json()["error"]["code"] == "AUDIENCE_INVALID"
        bad_source = api.post(
            "/api/v1/campaigns/",
            _payload(world, variable_mapping={"body": {"1": {"source": "contact.secret"}}}),
            format="json",
        )
        assert bad_source.status_code == 422
        assert bad_source.json()["error"]["code"] == "VARIABLE_MAPPING_INVALID"
        too_fast = api.post(
            "/api/v1/campaigns/", _payload(world, max_messages_per_minute=10**6), format="json"
        )
        assert too_fast.status_code == 422

    def test_business_id_in_body_cannot_escape_scope(self, world: dict[str, Any]) -> None:
        response = world["api"].post(
            "/api/v1/campaigns/",
            {**_payload(world), "business_id": str(world["eps"].pk)},
            format="json",
        )
        assert response.status_code == 422  # not an editable field; never trusted
        assert Campaign.objects.count() == 0

    def test_launch_requires_confirmation_and_passing_checks(self, world: dict[str, Any]) -> None:
        campaign = _create(world, variable_mapping={})
        unconfirmed = world["api"].post(
            f"/api/v1/campaigns/{campaign['id']}/launch/", {}, format="json"
        )
        assert unconfirmed.status_code == 400
        blocked = _launch(world, campaign["id"])
        assert blocked.status_code == 422
        body = blocked.json()["error"]
        assert body["code"] == "CAMPAIGN_NOT_LAUNCHABLE"
        failed = {c["code"] for c in body["details"]["checks"] if not c["ok"]}
        assert "VARIABLES_MAPPED" in failed
        assert Campaign.objects.get(pk=campaign["id"]).status == S.DRAFT

    def test_preview_shows_summary_and_exact_audience(self, world: dict[str, Any]) -> None:
        campaign = _create(world)
        preview = world["api"].get(f"/api/v1/campaigns/{campaign['id']}/preview/").json()
        assert preview["launchable"] is True
        assert preview["summary"]["recipients"] == 7
        assert preview["audience"]["count"] == 7
        assert preview["audience"]["count_is_exact"] is True
        assert preview["summary"]["estimated_duration_minutes"] == pytest.approx(7 / 60, abs=0.1)


class TestAudience:
    def _count(self, world: dict[str, Any], definition: dict[str, Any], api: Any = None) -> int:
        response = (api or world["api"]).post(
            "/api/v1/campaigns/audience-preview/", {"definition": definition}, format="json"
        )
        assert response.status_code == 200, response.json()
        return int(response.json()["count"])

    def test_filters(self, world: dict[str, Any]) -> None:
        kiosk = world["kiosk"]
        vip = factories.make_tag(kiosk, "VIP")
        factories.make_contact(
            kiosk, "919811100001", name="Pune VIP", attributes={"city": "Pune"}, tags=[vip]
        )
        factories.make_contact(kiosk, "919811100002", name="Old", last_message_days_ago=30)
        factories.make_contact(kiosk, "919811100003", name="Opted out", opt_in="opted_out")
        assert self._count(world, {}) == 9  # opted-out contact never included
        assert (
            self._count(
                world, {"conditions": [{"field": "tag", "op": "has", "value": str(vip.pk)}]}
            )
            == 1
        )
        assert (
            self._count(
                world,
                {
                    "conditions": [
                        {"field": "tag", "op": "not_has", "value": str(world["active"].pk)}
                    ]
                },
            )
            == 2
        )
        assert (
            self._count(
                world,
                {
                    "conditions": [
                        {"field": "attribute", "key": "city", "op": "eq", "value": "Pune"}
                    ]
                },
            )
            == 1
        )
        assert (
            self._count(
                world,
                {"conditions": [{"field": "last_activity", "op": "older_than_days", "value": 7}]},
            )
            == 1
        )
        assert (
            self._count(
                world, {"conditions": [{"field": "phone", "op": "starts_with", "value": "9198111"}]}
            )
            == 2
        )
        any_of = {
            "match": "any",
            "conditions": [
                {"field": "tag", "op": "has", "value": str(vip.pk)},
                {"field": "name", "op": "contains", "value": "old"},
            ],
        }
        assert self._count(world, any_of) == 2

    def test_business_isolation(self, world: dict[str, Any]) -> None:
        factories.make_contact(
            world["eps"], "919877700001", name="EPS customer", tags=[world["eps_tag"]]
        )
        # The same tag name in EPS matches only EPS contacts, and a Kiosk tag
        # id is invalid in the EPS context.
        eps_count = self._count(
            world,
            {"conditions": [{"field": "tag", "op": "has", "value": str(world["eps_tag"].pk)}]},
            api=world["eps_api"],
        )
        assert eps_count == 1
        response = world["eps_api"].post(
            "/api/v1/campaigns/audience-preview/",
            {
                "definition": {
                    "conditions": [{"field": "tag", "op": "has", "value": str(world["active"].pk)}]
                }
            },
            format="json",
        )
        assert response.status_code == 422

    def test_saved_audience_crud(self, world: dict[str, Any]) -> None:
        api = world["api"]
        definition = {
            "conditions": [{"field": "tag", "op": "has", "value": str(world["active"].pk)}]
        }
        created = api.post(
            "/api/v1/audiences/", {"name": "Active", "definition": definition}, format="json"
        )
        assert created.status_code == 201
        duplicate = api.post(
            "/api/v1/audiences/", {"name": "active", "definition": definition}, format="json"
        )
        assert duplicate.status_code == 409
        detail = api.get(f"/api/v1/audiences/{created.json()['id']}/").json()
        assert detail["preview"]["count"] == 7
        assert world["eps_api"].get(f"/api/v1/audiences/{created.json()['id']}/").status_code == 404
        campaign = _create(world, audience_id=created.json()["id"], audience_definition=definition)
        assert campaign["audience_id"] == created.json()["id"]


class TestExecution:
    def test_snapshot_rate_limited_ticks_status_mirroring_and_completion(
        self, world: dict[str, Any], fake_graph: FakeGraph, post_webhook: Any
    ) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        campaign = _create(world)
        launched = _launch(world, campaign["id"])
        assert launched.status_code == 202, launched.json()
        stored = Campaign.objects.get(pk=campaign["id"])
        assert stored.status == S.RUNNING  # eager start task: snapshot built
        assert stored.audience_count == 7

        # Snapshot stability: tagging a new contact afterwards changes nothing.
        factories.make_contact(world["kiosk"], "919800000999", name="Late", tags=[world["active"]])
        assert CampaignRecipient.objects.filter(campaign=stored).count() == 7

        first = engine.tick(stored.pk)  # 60/min * 5 s tick = 5 recipients
        assert first.dispatched == 5 and first.status == S.RUNNING
        assert Message.objects.filter(campaign=stored).count() == 5
        assert CampaignRecipient.objects.filter(campaign=stored, status=R.ACCEPTED).count() == 5
        assert CampaignRecipient.objects.filter(campaign=stored, status=R.PENDING).count() == 2
        second = engine.tick(stored.pk)
        assert second.dispatched == 2 and second.status == S.COMPLETED
        stored.refresh_from_db()
        assert stored.completed_at is not None

        sent = fake_graph.json_bodies("POST", "/messages")
        assert len(sent) == 7
        names = sorted(b["template"]["components"][0]["parameters"][0]["text"] for b in sent)
        assert names == sorted(f"Customer {i}" for i in range(7))

        # A delivered webhook moves exactly that recipient forward.
        message = Message.objects.filter(campaign=stored).first()
        assert message is not None
        post_webhook(
            load_fixture(
                "status.json",
                WABA_ID=phone.business_account.external_id,
                PHONE_NUMBER_ID=phone.external_id,
                WAMID=message.external_id,
                STATUS="delivered",
                TS=str(int(timezone.now().timestamp())),
            )
        )
        recipient = CampaignRecipient.objects.get(message=message)
        assert recipient.status == R.DELIVERED
        report = world["api"].get(f"/api/v1/campaigns/{stored.pk}/report/").json()
        assert report["progress"]["total"] == 7
        assert report["progress"]["delivered_or_later"] == 1
        assert report["progress"]["accepted_or_later"] == 7
        assert report["cost"]["state"] in {"none", "pending_rate"}  # no rate card loaded
        assert report["timing"]["duration_seconds"] is not None
        events = list(CampaignEvent.objects.filter(campaign=stored).values_list("event", flat=True))
        assert events[:4] == ["created", "launched", "snapshot_built", "started"]
        assert events[-1] == "completed"

    def test_missing_variables_are_skipped_never_sent_empty(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        factories.make_contact(world["kiosk"], "919800000111", name="", tags=[world["active"]])
        campaign = _create(world)
        preview = world["api"].get(f"/api/v1/campaigns/{campaign['id']}/preview/").json()
        assert preview["summary"]["recipients_missing_variables"] == 1
        assert _launch(world, campaign["id"]).status_code == 202
        _run_to_completion(campaign["id"])
        skipped = CampaignRecipient.objects.get(campaign_id=campaign["id"], status=R.SKIPPED)
        assert skipped.skip_reason == "VARIABLE_MISSING"
        assert skipped.message is None
        assert len(fake_graph.json_bodies("POST", "/messages")) == 7

    def test_fallback_is_used_when_value_missing(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        factories.make_contact(world["kiosk"], "919800000112", name="", tags=[world["active"]])
        campaign = _create(
            world, variable_mapping={"body": {"1": {"source": "contact.name", "fallback": "there"}}}
        )
        _launch(world, campaign["id"])
        _run_to_completion(campaign["id"])
        texts = [
            b["template"]["components"][0]["parameters"][0]["text"]
            for b in fake_graph.json_bodies("POST", "/messages")
        ]
        assert "there" in texts and len(texts) == 8

    def test_send_is_idempotent_per_recipient_attempt(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        campaign = _create(world)
        _launch(world, campaign["id"])
        stored = Campaign.objects.select_related("organization").get(pk=campaign["id"])
        recipient = CampaignRecipient.objects.filter(campaign=stored).order_by("pk").first()
        assert recipient is not None
        # A redelivered task / duplicated tick handing the same recipient over twice.
        engine._send_one(stored, recipient)
        engine._send_one(stored, recipient)
        assert Message.objects.filter(campaign=stored).count() == 1
        assert len(fake_graph.json_bodies("POST", "/messages")) == 1
        recipient.refresh_from_db()
        assert recipient.message is not None and recipient.status == R.ACCEPTED

    def test_orphaned_claim_is_recovered_once(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        campaign = _create(world)
        _launch(world, campaign["id"])
        # Worker crashed after claiming (QUEUED) but before handing over.
        orphan = CampaignRecipient.objects.filter(campaign_id=campaign["id"]).order_by("pk").first()
        assert orphan is not None
        CampaignRecipient.objects.filter(pk=orphan.pk).update(
            status=R.QUEUED, status_changed_at=timezone.now() - timedelta(minutes=5)
        )
        _run_to_completion(campaign["id"])
        assert Message.objects.filter(campaign_id=campaign["id"]).count() == 7
        orphan.refresh_from_db()
        assert orphan.status == R.ACCEPTED

    def test_pause_resume_cancel(self, world: dict[str, Any], fake_graph: FakeGraph) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        campaign = _create(world)
        _launch(world, campaign["id"])
        engine.tick(campaign["id"])
        paused = world["api"].post(f"/api/v1/campaigns/{campaign['id']}/pause/")
        assert paused.status_code == 200 and paused.json()["campaign"]["status"] == S.PAUSED
        assert engine.tick(campaign["id"]).dispatched == 0  # pause stops new dispatch
        assert Message.objects.filter(campaign_id=campaign["id"]).count() == 5
        again = world["api"].post(f"/api/v1/campaigns/{campaign['id']}/pause/")
        assert again.status_code == 409
        resumed = world["api"].post(f"/api/v1/campaigns/{campaign['id']}/resume/")
        assert resumed.json()["campaign"]["status"] == S.RUNNING
        engine.tick(campaign["id"])
        assert Message.objects.filter(campaign_id=campaign["id"]).count() == 7

        second = _create(world, name="To cancel")
        _launch(world, second["id"])
        engine.tick(second["id"])
        cancelled = world["api"].post(f"/api/v1/campaigns/{second['id']}/cancel/")
        assert cancelled.status_code == 200
        assert cancelled.json()["pending_cancelled"] == 2
        assert engine.tick(second["id"]).dispatched == 0
        counts = cancelled.json()["campaign"]["progress"]["by_status"]
        assert counts["cancelled"] == 2 and counts["accepted"] == 5  # accepted stay recorded

    def test_cancel_stops_messages_not_yet_handed_to_provider(
        self, world: dict[str, Any], fake_graph: FakeGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.messaging.services import outbound

        monkeypatch.setattr(outbound, "enqueue_dispatch", lambda *a, **k: None)
        campaign = _create(world)
        _launch(world, campaign["id"])
        engine.tick(campaign["id"])  # 5 messages QUEUED, never dispatched
        assert (
            Message.objects.filter(campaign_id=campaign["id"], status=MessageStatus.QUEUED).count()
            == 5
        )
        result = engine.cancel(campaign["id"], actor=None)
        assert result == {"pending_cancelled": 2, "queued_stopped": 5}
        assert not Message.objects.filter(
            campaign_id=campaign["id"], status=MessageStatus.QUEUED
        ).exists()
        assert (
            CampaignRecipient.objects.filter(campaign_id=campaign["id"], status=R.CANCELLED).count()
            == 7
        )
        assert fake_graph.json_bodies("POST", "/messages") == []

    def test_retry_only_safe_failures(
        self, world: dict[str, Any], fake_graph: FakeGraph, post_webhook: Any
    ) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        campaign = _create(world)
        _launch(world, campaign["id"])
        _run_to_completion(campaign["id"])
        recipients = list(
            CampaignRecipient.objects.filter(campaign_id=campaign["id"]).order_by("pk")
        )
        # One recipient hit a rate limit, one timed out with unknown outcome,
        # one is an invalid recipient (as recorded by the pipeline).
        CampaignRecipient.objects.filter(pk=recipients[0].pk).update(
            status=R.FAILED, failure_category="rate_limit", error_code="PROVIDER_RATE_LIMITED"
        )
        CampaignRecipient.objects.filter(pk=recipients[1].pk).update(
            status=R.FAILED, failure_category="timeout", error_code="PROVIDER_OUTCOME_UNKNOWN"
        )
        CampaignRecipient.objects.filter(pk=recipients[2].pk).update(
            status=R.FAILED, failure_category="invalid_recipient", error_code="131026"
        )
        old_message = recipients[0].message
        assert old_message is not None
        failures = world["api"].get(f"/api/v1/campaigns/{campaign['id']}/failures/").json()
        assert failures["retryable_recipients"] == 1
        retry = world["api"].post(f"/api/v1/campaigns/{campaign['id']}/retry-failed/")
        assert retry.status_code == 200 and retry.json()["recipients"] == 1
        assert retry.json()["campaign"]["status"] == S.RUNNING
        _run_to_completion(campaign["id"])
        first = CampaignRecipient.objects.get(pk=recipients[0].pk)
        assert first.attempts == 2 and first.status == R.ACCEPTED
        assert first.message_id != old_message.pk
        assert Message.objects.filter(campaign_id=campaign["id"]).count() == 8
        # A late webhook for the *old* attempt never overwrites the new one.
        post_webhook(
            load_fixture(
                "status_failed.json",
                WABA_ID=phone.business_account.external_id,
                PHONE_NUMBER_ID=phone.external_id,
                WAMID=old_message.external_id or "",
                TS=str(int(timezone.now().timestamp())),
            )
        )
        first.refresh_from_db()
        assert first.status == R.ACCEPTED
        assert CampaignRecipient.objects.get(pk=recipients[1].pk).status == R.FAILED

    def test_provider_rejection_is_recorded_and_categorised(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on(
            "POST", f"/{phone.external_id}/messages", graph_error(400, 131026, "undeliverable")
        )
        campaign = _create(world)
        _launch(world, campaign["id"])
        _run_to_completion(campaign["id"])
        failures = world["api"].get(f"/api/v1/campaigns/{campaign['id']}/failures/").json()
        [group] = failures["groups"]
        assert group["category"] == "invalid_recipient" and group["count"] == 7
        assert group["retryable"] is False
        assert "EAAtest" not in json.dumps(failures)  # never leaks credentials
        retry = world["api"].post(f"/api/v1/campaigns/{campaign['id']}/retry-failed/")
        assert retry.json()["recipients"] == 0

    def test_disconnected_number_fails_campaign_and_resume_rechecks(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        campaign = _create(world)
        _launch(world, campaign["id"])
        type(phone).objects.filter(pk=phone.pk).update(status="disconnected")
        result = engine.tick(campaign["id"])
        assert result.status == S.FAILED
        stored = Campaign.objects.get(pk=campaign["id"])
        assert stored.failure_code == "SEND_BLOCKED"
        assert Message.objects.filter(campaign=stored).count() == 0
        blocked = world["api"].post(f"/api/v1/campaigns/{campaign['id']}/resume/")
        assert blocked.status_code == 422
        type(phone).objects.filter(pk=phone.pk).update(status="connected")
        assert world["api"].post(f"/api/v1/campaigns/{campaign['id']}/resume/").status_code == 200
        _run_to_completion(campaign["id"])
        assert Message.objects.filter(campaign=stored).count() == 7


class TestScheduling:
    def test_future_campaign_waits_for_beat(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        when = (timezone.now() + timedelta(hours=2)).replace(microsecond=0)
        campaign = _create(world, scheduled_at=when.isoformat(), timezone="Asia/Kolkata")
        response = _launch(world, campaign["id"])
        assert response.status_code == 202
        assert response.json()["campaign"]["status"] == S.SCHEDULED
        assert engine.start_due_campaigns() == 0
        Campaign.objects.filter(pk=campaign["id"]).update(
            scheduled_at=timezone.now() - timedelta(minutes=1)
        )
        assert engine.start_due_campaigns() == 1
        assert Campaign.objects.get(pk=campaign["id"]).status == S.RUNNING

    def test_naive_schedule_is_interpreted_in_campaign_timezone(
        self, world: dict[str, Any]
    ) -> None:
        campaign = _create(world, scheduled_at="2030-01-15T09:30:00", timezone="Asia/Kolkata")
        stored = Campaign.objects.get(pk=campaign["id"])
        assert stored.scheduled_at is not None
        assert stored.scheduled_at.isoformat().startswith("2030-01-15T04:00:00")

    def test_past_schedule_blocks_launch(self, world: dict[str, Any]) -> None:
        campaign = _create(world, scheduled_at="2020-01-01T00:00:00+00:00")
        response = _launch(world, campaign["id"])
        assert response.status_code == 422
        failed = {c["code"] for c in response.json()["error"]["details"]["checks"] if not c["ok"]}
        assert "SCHEDULE" in failed


class TestSecurity:
    def test_business_isolation_and_permissions(
        self, world: dict[str, Any], organization: Any
    ) -> None:
        kiosk_campaign = _create(world)
        eps_campaign = (
            world["eps_api"]
            .post(
                "/api/v1/campaigns/",
                {
                    **_payload(world),
                    "phone_number_id": str(world["eps_phone"].pk),
                    "template_id": str(world["eps_template"].pk),
                    "audience_definition": {},
                },
                format="json",
            )
            .json()
        )
        kiosk_agent = factories.make_member(organization, "manager", businesses=[world["kiosk"]])
        client = api_client_for(kiosk_agent, organization)
        listed = {c["id"] for c in client.get("/api/v1/campaigns/").json()["results"]}
        assert listed == {kiosk_campaign["id"]}
        for path in ("", "report/", "recipients/", "failures/", "preview/", "events/"):
            assert client.get(f"/api/v1/campaigns/{eps_campaign['id']}/{path}").status_code == 404
        assert (
            client.post(
                f"/api/v1/campaigns/{eps_campaign['id']}/launch/", {"confirm": True}, format="json"
            ).status_code
            == 404
        )
        viewer = factories.make_member(organization, "viewer")
        viewer_api = api_client_for(viewer, organization, world["kiosk"])
        assert viewer_api.get("/api/v1/campaigns/").status_code == 200
        assert (
            viewer_api.post("/api/v1/campaigns/", _payload(world), format="json").status_code == 403
        )
        assert (
            viewer_api.post(
                f"/api/v1/campaigns/{kiosk_campaign['id']}/launch/",
                {"confirm": True},
                format="json",
            ).status_code
            == 403
        )

    def test_audit_trail(self, world: dict[str, Any]) -> None:
        from apps.audit.models import AuditLog

        campaign = _create(world)
        world["api"].patch(
            f"/api/v1/campaigns/{campaign['id']}/",
            {"audience_definition": {"conditions": []}, "name": "Renamed"},
            format="json",
        )
        _launch(world, campaign["id"])
        world["api"].post(f"/api/v1/campaigns/{campaign['id']}/pause/")
        world["api"].post(f"/api/v1/campaigns/{campaign['id']}/resume/")
        world["api"].post(f"/api/v1/campaigns/{campaign['id']}/cancel/")
        actions = set(
            AuditLog.objects.filter(resource_id=campaign["id"]).values_list("action", flat=True)
        )
        assert {
            "campaign.created",
            "campaign.updated",
            "campaign.audience_changed",
            "campaign.launched",
            "campaign.paused",
            "campaign.resumed",
            "campaign.cancelled",
        } <= actions
        edit_after_launch = world["api"].patch(
            f"/api/v1/campaigns/{campaign['id']}/", {"name": "x"}, format="json"
        )
        assert edit_after_launch.status_code == 409


class TestRealtime:
    def test_progress_and_lifecycle_events_are_published_to_owning_business_only(
        self, world: dict[str, Any], fake_graph: FakeGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[tuple[str, str, dict[str, Any]]] = []
        monkeypatch.setattr(
            publisher,
            "_send",
            lambda business_id, event, data: sent.append((str(business_id), event, data)),
        )
        phone = world["kiosk_phone"]
        fake_graph.on("POST", f"/{phone.external_id}/messages", accept_send)
        campaign = _create(world)
        _launch(world, campaign["id"])
        engine.tick(campaign["id"])
        world["api"].post(f"/api/v1/campaigns/{campaign['id']}/pause/")
        world["api"].post(f"/api/v1/campaigns/{campaign['id']}/resume/")
        engine.tick(campaign["id"])
        campaign_events = [(e, d) for b, e, d in sent if e.startswith("campaign.")]
        assert {b for b, e, _ in sent if e.startswith("campaign.")} == {str(world["kiosk"].pk)}
        statuses = [d["status"] for e, d in campaign_events if e == "campaign.updated"]
        assert statuses[:2] == [S.QUEUED, S.RUNNING]
        assert S.PAUSED in statuses and statuses[-1] == S.COMPLETED
        progress_events = [d["progress"] for e, d in campaign_events if e == "campaign.progress"]
        assert progress_events, "tick publishes real progress"
        # Progress numbers are the persisted counters, e.g. 5/7 after the first tick.
        assert any(p["processed"] == 5 and p["total"] == 7 for p in progress_events)
        assert campaign_events[-1][1]["progress"]["processed"] == 7
