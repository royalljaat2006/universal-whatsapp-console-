"""Cycle 2 management API: contacts, tags, notes, assignment, business access,
search/filters, templates — with business isolation checks throughout."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.businesses.models import BusinessAccess
from apps.conversations.models import Conversation, ConversationAssignment
from apps.messaging.models import Direction, Message
from apps.organizations.models import Membership
from tests import factories
from tests.conftest import api_client_for, load_fixture
from tests.fakes import FakeGraph

pytestmark = pytest.mark.django_db(transaction=True)


def _inbound(
    post_webhook: Any, phone: Any, wamid: str, body: str = "hello", sender: str = "16505551234"
) -> None:
    payload = load_fixture(
        "inbound_text.json",
        WABA_ID=phone.business_account.external_id,
        PHONE_NUMBER_ID=phone.external_id,
        WAMID=wamid,
        TS=str(int(timezone.now().timestamp())),
    )
    value = payload["entry"][0]["changes"][0]["value"]
    value["messages"][0]["text"]["body"] = body
    value["messages"][0]["from"] = sender
    value["messages"][0].pop("from_user_id")
    value["contacts"][0]["wa_id"] = sender
    value["contacts"][0].pop("user_id")
    post_webhook(payload)


@pytest.fixture
def world(post_webhook: Any, owner: Any, organization: Any) -> dict[str, Any]:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")
    kiosk_phone = factories.make_phone_number(factories.make_waba(organization, business=kiosk))
    eps_phone = factories.make_phone_number(factories.make_waba(organization, business=eps))
    _inbound(post_webhook, kiosk_phone, "wamid.K1", "Where is my refund?")
    _inbound(post_webhook, eps_phone, "wamid.E1", "EPS question")
    kiosk_agent = factories.make_member(organization, "agent", businesses=[kiosk])
    kiosk_manager = factories.make_member(organization, "manager", businesses=[kiosk])
    return {
        "kiosk": kiosk,
        "eps": eps,
        "kiosk_phone": kiosk_phone,
        "eps_phone": eps_phone,
        "kiosk_agent_user": kiosk_agent,
        "kiosk_agent": api_client_for(kiosk_agent, organization, kiosk),
        "kiosk_manager": api_client_for(kiosk_manager, organization, kiosk),
        "owner": api_client_for(owner, organization),
        "kiosk_conv": Conversation.objects.get(business=kiosk),
        "eps_conv": Conversation.objects.get(business=eps),
    }


class TestContacts:
    def test_edit_attributes_and_history(self, world: dict[str, Any]) -> None:
        contact = world["kiosk_conv"].contact
        client = world["kiosk_agent"]
        response = client.patch(
            f"/api/v1/contacts/{contact.pk}/",
            {"display_name": "Sheena N.", "attributes": {"customer_id": "C-9"}},
            format="json",
        )
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["display_name"] == "Sheena N." and body["attributes"] == {"customer_id": "C-9"}
        assert body["conversation_count"] == 1 and body["first_seen_at"]
        history = client.get(f"/api/v1/contacts/{contact.pk}/history/").json()
        assert [c["id"] for c in history] == [str(world["kiosk_conv"].pk)]

    def test_invalid_attribute_name_is_rejected(self, world: dict[str, Any]) -> None:
        contact = world["kiosk_conv"].contact
        response = world["kiosk_agent"].patch(
            f"/api/v1/contacts/{contact.pk}/", {"attributes": {"bad key": "x"}}, format="json"
        )
        assert response.status_code == 400

    def test_notes_crud(self, world: dict[str, Any]) -> None:
        contact = world["kiosk_conv"].contact
        client = world["kiosk_agent"]
        created = client.post(
            f"/api/v1/contacts/{contact.pk}/notes/", {"body": "VIP customer"}, format="json"
        )
        assert created.status_code == 201
        assert (
            client.get(f"/api/v1/contacts/{contact.pk}/notes/").json()[0]["body"] == "VIP customer"
        )
        deleted = client.delete(f"/api/v1/contacts/{contact.pk}/notes/{created.json()['id']}/")
        assert deleted.status_code == 204

    def test_cross_business_contact_access_is_404(self, world: dict[str, Any]) -> None:
        eps_contact = world["eps_conv"].contact
        client = world["kiosk_agent"]
        assert client.get(f"/api/v1/contacts/{eps_contact.pk}/").status_code == 404
        assert (
            client.patch(
                f"/api/v1/contacts/{eps_contact.pk}/", {"display_name": "x"}, format="json"
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v1/contacts/{eps_contact.pk}/notes/", {"body": "x"}, format="json"
            ).status_code
            == 404
        )

    def test_viewer_cannot_edit(self, world: dict[str, Any], organization: Any) -> None:
        viewer = api_client_for(factories.make_member(organization, "viewer"), organization)
        contact = world["kiosk_conv"].contact
        assert (
            viewer.patch(
                f"/api/v1/contacts/{contact.pk}/", {"display_name": "x"}, format="json"
            ).status_code
            == 403
        )


class TestTags:
    def test_create_rename_delete_and_uniqueness(self, world: dict[str, Any]) -> None:
        client = world["kiosk_agent"]
        created = client.post("/api/v1/tags/", {"name": "VIP", "color": "red"}, format="json")
        assert created.status_code == 201
        assert client.post("/api/v1/tags/", {"name": "vip"}, format="json").status_code == 409
        tag_id = created.json()["id"]
        assert (
            client.patch(f"/api/v1/tags/{tag_id}/", {"name": "Priority"}, format="json").json()[
                "name"
            ]
            == "Priority"
        )
        assert [t["name"] for t in client.get("/api/v1/tags/?q=prio").json()] == ["Priority"]
        assert client.delete(f"/api/v1/tags/{tag_id}/").status_code == 204

    def test_same_name_allowed_in_other_business(
        self, world: dict[str, Any], organization: Any
    ) -> None:
        world["kiosk_agent"].post("/api/v1/tags/", {"name": "VIP"}, format="json")
        eps_agent = api_client_for(
            factories.make_member(organization, "agent", businesses=[world["eps"]]),
            organization,
            world["eps"],
        )
        assert eps_agent.post("/api/v1/tags/", {"name": "VIP"}, format="json").status_code == 201
        assert len(world["kiosk_agent"].get("/api/v1/tags/").json()) == 1

    def test_assign_filter_and_isolation(self, world: dict[str, Any], organization: Any) -> None:
        client = world["kiosk_agent"]
        tag = client.post("/api/v1/tags/", {"name": "Refund"}, format="json").json()
        conv = world["kiosk_conv"]
        tagged = client.post(
            f"/api/v1/conversations/{conv.pk}/tags/", {"tag_id": tag["id"]}, format="json"
        )
        assert [t["name"] for t in tagged.json()["tags"]] == ["Refund"]
        client.post(
            f"/api/v1/contacts/{conv.contact_id}/tags/", {"tag_id": tag["id"]}, format="json"
        )
        assert [
            c["id"] for c in client.get(f"/api/v1/conversations/?tag={tag['id']}").json()["results"]
        ] == [str(conv.pk)]
        assert len(client.get(f"/api/v1/contacts/?tag={tag['id']}").json()["results"]) == 1
        # A Kiosk tag cannot be applied to an EPS conversation, even by the owner.
        owner = world["owner"]
        response = owner.post(
            f"/api/v1/conversations/{world['eps_conv'].pk}/tags/",
            {"tag_id": tag["id"]},
            format="json",
        )
        assert response.status_code == 404
        removed = client.delete(f"/api/v1/conversations/{conv.pk}/tags/{tag['id']}/")
        assert removed.json()["tags"] == []


class TestAssignment:
    def test_assign_reassign_unassign_with_history(
        self, world: dict[str, Any], organization: Any
    ) -> None:
        manager = world["kiosk_manager"]
        agent_membership = Membership.objects.get(user=world["kiosk_agent_user"])
        conv = world["kiosk_conv"]
        options = manager.get("/api/v1/conversations/assignees/").json()
        assert str(agent_membership.pk) in [m["membership_id"] for m in options["members"]]
        team = manager.post("/api/v1/teams/", {"name": "Refunds"}, format="json")
        assert team.status_code == 201
        first = manager.post(
            f"/api/v1/conversations/{conv.pk}/assign/",
            {"membership_id": str(agent_membership.pk)},
            format="json",
        )
        assert first.json()["assignee"]["membership_id"] == str(agent_membership.pk)
        second = manager.post(
            f"/api/v1/conversations/{conv.pk}/assign/",
            {"team_id": team.json()["id"]},
            format="json",
        )
        assert second.json()["assignee"] is None and second.json()["team"]["name"] == "Refunds"
        manager.post(f"/api/v1/conversations/{conv.pk}/assign/", {}, format="json")
        history = manager.get(f"/api/v1/conversations/{conv.pk}/assignments/").json()
        assert len(history) == 3
        assert history[-1]["new_membership_id"] == str(agent_membership.pk)
        assert ConversationAssignment.objects.filter(conversation=conv).count() == 3
        mine = world["kiosk_agent"].get("/api/v1/conversations/?assigned=none").json()["results"]
        assert [c["id"] for c in mine] == [str(conv.pk)]

    def test_agent_cannot_assign(self, world: dict[str, Any]) -> None:
        response = world["kiosk_agent"].post(
            f"/api/v1/conversations/{world['kiosk_conv'].pk}/assign/", {}, format="json"
        )
        assert response.status_code == 403

    def test_assignee_without_business_access_is_rejected(
        self, world: dict[str, Any], organization: Any
    ) -> None:
        eps_only = factories.make_member(organization, "agent", businesses=[world["eps"]])
        membership = Membership.objects.get(user=eps_only)
        response = world["kiosk_manager"].post(
            f"/api/v1/conversations/{world['kiosk_conv'].pk}/assign/",
            {"membership_id": str(membership.pk)},
            format="json",
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_ASSIGNEE"

    def test_cannot_assign_foreign_business_conversation(self, world: dict[str, Any]) -> None:
        response = world["kiosk_manager"].post(
            f"/api/v1/conversations/{world['eps_conv'].pk}/assign/", {}, format="json"
        )
        assert response.status_code == 404


class TestBusinessAccess:
    def test_grant_and_revoke(self, world: dict[str, Any], organization: Any) -> None:
        owner = world["owner"]
        agent = Membership.objects.get(user=world["kiosk_agent_user"])
        eps = world["eps"]
        listing = owner.get(f"/api/v1/businesses/{eps.pk}/access/").json()
        row = next(r for r in listing["members"] if r["membership_id"] == str(agent.pk))
        assert row["has_access"] is False and row["manageable"] is True

        assert (
            owner.post(
                f"/api/v1/businesses/{eps.pk}/access/",
                {"membership_id": str(agent.pk)},
                format="json",
            ).status_code
            == 204
        )
        agent_any = api_client_for(world["kiosk_agent_user"], organization)
        assert agent_any.get(f"/api/v1/conversations/{world['eps_conv'].pk}/").status_code == 200

        assert owner.delete(f"/api/v1/businesses/{eps.pk}/access/{agent.pk}/").status_code == 204
        assert agent_any.get(f"/api/v1/conversations/{world['eps_conv'].pk}/").status_code == 404

    def test_user_a_kiosk_only_cannot_access_eps(
        self, world: dict[str, Any], organization: Any
    ) -> None:
        # No default business header: the EPS header below is what is sent.
        client = api_client_for(world["kiosk_agent_user"], organization)
        eps_ctx = client.get("/api/v1/conversations/", HTTP_X_BUSINESS_ID=str(world["eps"].pk))
        assert eps_ctx.status_code == 404
        eps_id = str(world["eps_conv"].pk)
        assert eps_id not in [
            c["id"] for c in client.get("/api/v1/conversations/").json()["results"]
        ]
        assert client.get(f"/api/v1/whatsapp/numbers/{world['eps_phone'].pk}/").status_code == 404
        assert [b["name"] for b in client.get("/api/v1/businesses/").json()] == ["Kiosk"]

    def test_no_self_grant_and_owner_protected(
        self, world: dict[str, Any], organization: Any, owner: Any
    ) -> None:
        admin_user = factories.make_member(organization, "admin")
        admin = api_client_for(admin_user, organization)
        admin_membership = Membership.objects.get(user=admin_user)
        owner_membership = Membership.objects.get(user=owner)
        eps = world["eps"]
        assert (
            admin.delete(f"/api/v1/businesses/{eps.pk}/access/{admin_membership.pk}/").status_code
            == 403
        )
        assert (
            admin.delete(f"/api/v1/businesses/{eps.pk}/access/{owner_membership.pk}/").status_code
            == 403
        )
        assert (
            admin.patch(
                f"/api/v1/organizations/current/members/{admin_membership.pk}/",
                {"business_scope": "all"},
                format="json",
            ).status_code
            == 403
        )

    def test_revoke_from_all_scope_member_converts_to_explicit_grants(
        self, world: dict[str, Any], organization: Any
    ) -> None:
        admin_user = factories.make_member(organization, "admin")
        target = Membership.objects.get(user=admin_user)
        assert target.business_scope == "all"
        world["owner"].delete(f"/api/v1/businesses/{world['eps'].pk}/access/{target.pk}/")
        target.refresh_from_db()
        assert target.business_scope == "selected"
        assert set(
            BusinessAccess.objects.filter(membership=target).values_list(
                "business__name", flat=True
            )
        ) == {"Kiosk"}

    def test_manager_cannot_manage_access(self, world: dict[str, Any]) -> None:
        assert (
            world["kiosk_manager"]
            .get(f"/api/v1/businesses/{world['kiosk'].pk}/access/")
            .status_code
            == 403
        )


class TestSearchAndFilters:
    def test_search_by_message_text_name_phone_and_id(
        self, world: dict[str, Any], post_webhook: Any
    ) -> None:
        _inbound(
            post_webhook, world["kiosk_phone"], "wamid.K2", "Invoice please", sender="919811112222"
        )
        client = world["kiosk_agent"]

        def ids(query: str) -> list[str]:
            return [
                c["id"] for c in client.get(f"/api/v1/conversations/?{query}").json()["results"]
            ]

        refund = str(world["kiosk_conv"].pk)
        assert ids("q=refund") == [refund]
        assert ids("q=Sheena")  # contact name (from WhatsApp profile)
        assert len(ids("q=98111")) == 1
        assert ids(f"q={refund}") == [refund]
        assert ids("q=EPS question") == []  # other business never matches

    def test_unread_status_and_date_filters(self, world: dict[str, Any]) -> None:
        client = world["kiosk_agent"]
        conv = world["kiosk_conv"]
        assert [
            c["id"] for c in client.get("/api/v1/conversations/?unread=1").json()["results"]
        ] == [str(conv.pk)]
        client.post(f"/api/v1/conversations/{conv.pk}/read/")
        assert client.get("/api/v1/conversations/?unread=1").json()["results"] == []
        yesterday = (timezone.now() - timedelta(days=1)).date().isoformat()
        assert (
            len(client.get(f"/api/v1/conversations/?date_from={yesterday}").json()["results"]) == 1
        )
        assert client.get(f"/api/v1/conversations/?date_to={yesterday}").json()["results"] == []
        today = timezone.localdate().isoformat()
        # date_to=<today> includes the whole day, not just midnight.
        assert len(client.get(f"/api/v1/conversations/?date_to={today}").json()["results"]) == 1
        assert client.get("/api/v1/conversations/?date_from=not-a-date").status_code == 400

    def test_list_does_not_scale_queries_with_rows(
        self, world: dict[str, Any], post_webhook: Any, django_assert_max_num_queries: Any
    ) -> None:
        for i in range(8):
            _inbound(post_webhook, world["kiosk_phone"], f"wamid.N{i}", sender=f"91999000{i:04d}")
        with django_assert_max_num_queries(12):
            response = world["kiosk_agent"].get("/api/v1/conversations/")
        assert len(response.json()["results"]) == 9

    def test_timeline_includes_status_history(self, world: dict[str, Any]) -> None:
        conv = world["kiosk_conv"]
        message = Message.objects.get(conversation=conv, direction=Direction.INBOUND)
        client = world["kiosk_agent"]
        timeline = client.get(f"/api/v1/messages/?conversation={conv.pk}").json()["results"]
        assert timeline[0]["id"] == str(message.pk) and timeline[0]["attachments"] == []
        assert client.get(f"/api/v1/messages/{message.pk}/status-history/").json() == []


class TestTemplatesApi:
    def test_list_filters_and_detail_structure(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        waba = world["kiosk_phone"].business_account
        factories.make_rich_template(waba)
        factories.make_template(waba, name="welcome", status="REJECTED")
        client = world["kiosk_agent"]
        assert [
            t["name"] for t in client.get("/api/v1/templates/?sendable=1").json()["results"]
        ] == ["order_shipped_v1"]
        assert [t["name"] for t in client.get("/api/v1/templates/?q=welc").json()["results"]] == [
            "welcome"
        ]
        template_id = client.get("/api/v1/templates/?q=order").json()["results"][0]["id"]
        detail = client.get(f"/api/v1/templates/{template_id}/").json()
        params = [(p["component"], p["key"], p["kind"]) for p in detail["structure"]["parameters"]]
        assert params == [
            ("header", "media", "image"),
            ("body", "1", "text"),
            ("body", "2", "text"),
            ("button", "0", "text"),
        ]
        # EPS agent cannot see Kiosk templates
        assert (
            world["owner"]
            .get("/api/v1/templates/", HTTP_X_BUSINESS_ID=str(world["eps"].pk))
            .json()["results"]
            == []
        )

    def test_business_template_sync_records_state(
        self, world: dict[str, Any], fake_graph: FakeGraph
    ) -> None:
        waba = world["kiosk_phone"].business_account
        fake_graph.on(
            "GET",
            f"/{waba.external_id}/message_templates",
            {
                "data": [
                    {
                        "id": "t9",
                        "name": "hello",
                        "language": "en",
                        "category": "UTILITY",
                        "status": "APPROVED",
                        "components": [],
                    }
                ]
            },
        )
        response = world["kiosk_manager"].post("/api/v1/templates/sync/")
        assert response.status_code == 202
        waba.refresh_from_db()
        assert waba.template_sync_state == "succeeded" and waba.templates_last_synced_at
        assert world["kiosk_agent"].post("/api/v1/templates/sync/").status_code == 403
