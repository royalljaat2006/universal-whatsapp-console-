"""Template Studio: validation against Meta's documented rules, draft lifecycle,
the confirmed submit, duplicates, API failures and status honesty. The Graph API
is the loud-failing fake: any call that is not explicitly routed fails the test,
so nothing here can reach Meta."""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.apikeys.services import create_api_key
from apps.audit.models import AuditLog
from apps.templates import guidance, studio
from apps.templates.models import MessageTemplate, TemplateDraft
from tests import factories
from tests.conftest import api_client_for
from tests.fakes import FakeGraph, graph_error, raise_exc

pytestmark = pytest.mark.django_db(transaction=True)

BODY = "Hi {{1}}, your order {{2}} has shipped and should arrive soon."
CONTENT: dict[str, Any] = {
    "header_text": "",
    "body": BODY,
    "examples": {"1": "Asha", "2": "ORD-1042"},
    "footer": "",
    "buttons": [],
}


def check(**over: Any) -> studio.Validation:
    args: dict[str, Any] = {
        "name": "order_shipped",
        "language": "en_US",
        "category": "UTILITY",
        "parameter_format": "POSITIONAL",
        "content": CONTENT,
    }
    args.update(over)
    return studio.validate(**args)


def codes(v: studio.Validation) -> set[str]:
    return {i.code for i in v.errors}


def with_content(**changes: Any) -> dict[str, Any]:
    return {**CONTENT, **changes}


# ---------------------------------------------------------------- rules (pure)


class TestRules:
    def test_a_valid_template_has_no_errors_and_builds_meta_components(self) -> None:
        v = check()
        assert v.ok and not v.errors
        assert v.components == [
            {
                "type": "BODY",
                "text": BODY,
                "example": {"body_text": [["Asha", "ORD-1042"]]},
            }
        ]
        assert (
            v.preview["body"] == "Hi Asha, your order ORD-1042 has shipped and should arrive soon."
        )
        assert any(w.code == "meta_review" for w in v.warnings)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("", "name_required"),
            ("Order Shipped", "name_format"),
            ("order-shipped", "name_format"),
            ("a" * 513, "name_length"),
        ],
    )
    def test_name_rules(self, name: str, expected: str) -> None:
        assert expected in codes(check(name=name))

    def test_language_and_category_must_be_supported(self) -> None:
        assert "language_unsupported" in codes(check(language="xx_YY"))
        assert "category_unsupported" in codes(check(category="BANANA"))

    def test_authentication_is_stated_as_unsupported_not_silently_accepted(self) -> None:
        v = check(category="AUTHENTICATION")
        issue = next(i for i in v.errors if i.code == "category_unsupported")
        assert "WhatsApp Manager" in issue.message

    def test_body_is_required_and_limited_to_1024(self) -> None:
        assert "body_required" in codes(check(content=with_content(body="", examples={})))
        long = check(content=with_content(body="word " * 250 + "end", examples={}))
        assert "body_length" in codes(long)
        assert check(content=with_content(body="x" * 1024, examples={})).counts["body"] == {
            "used": 1024,
            "limit": 1024,
        }
        assert "body_length" not in codes(check(content=with_content(body="x" * 1024, examples={})))

    def test_header_is_limited_to_60_and_one_variable(self) -> None:
        assert "header_length" in codes(check(content=with_content(header_text="h" * 61)))
        too_many = check(
            content=with_content(header_text="Hi {{1}} and {{2}}", examples={"1": "a", "2": "b"})
        )
        assert "too_many_variables" in codes(too_many)
        ok = check(
            content=with_content(header_text="Order {{1}} update", examples={"1": "A1", "2": "B"})
        )
        assert "too_many_variables" not in codes(ok)
        header = next(c for c in ok.components if c["type"] == "HEADER")
        assert header["format"] == "TEXT" and header["example"] == {"header_text": ["A1"]}

    def test_footer_is_limited_to_60_and_cannot_hold_variables(self) -> None:
        assert "footer_length" in codes(check(content=with_content(footer="f" * 61)))
        assert "footer_variable" in codes(check(content=with_content(footer="Hi {{1}}")))

    @pytest.mark.parametrize(
        ("body", "examples", "expected"),
        [
            ("Hi {{1}} and {{3}} are here today", {"1": "a", "3": "b"}, "variable_sequence"),
            ("Hi {{2}} and {{3}} are here today", {"2": "a", "3": "b"}, "variable_sequence"),
            ("Hi { {1}} are here today", {}, "variable_format"),
            ("Hi {{ 1 }} are here today", {"1": "a"}, "variable_format"),
            ("Hi {{1} are here today", {}, "variable_format"),
            ("Hi {{name}} are here today", {"name": "a"}, "variable_numbering"),
            ("{{1}} is here today", {"1": "a"}, "variable_at_edge"),
            ("Your code is here {{1}}", {"1": "a"}, "variable_at_edge"),
            ("Hi {{1}} welcome", {}, "example_missing"),
        ],
    )
    def test_positional_variable_rules(self, body: str, examples: dict, expected: str) -> None:
        assert expected in codes(check(content=with_content(body=body, examples=examples)))

    def test_named_variables_use_lowercase_and_underscores_and_need_examples(self) -> None:
        good = check(
            parameter_format="NAMED",
            content=with_content(
                body="Hi {{first_name}}, welcome aboard today", examples={"first_name": "Asha"}
            ),
        )
        assert good.ok
        body = next(c for c in good.components if c["type"] == "BODY")
        assert body["example"] == {
            "body_text_named_params": [{"param_name": "first_name", "example": "Asha"}]
        }
        bad = check(
            parameter_format="NAMED",
            content=with_content(
                body="Hi {{FirstName}}, welcome aboard today", examples={"FirstName": "A"}
            ),
        )
        assert "variable_name" in codes(bad)

    def test_every_variable_needs_an_example(self) -> None:
        v = check(content=with_content(examples={"1": "Asha"}))
        assert [i.field for i in v.errors if i.code == "example_missing"] == ["body.2"]

    def test_button_rules(self) -> None:
        many = [{"type": "QUICK_REPLY", "text": f"B{i}"} for i in range(11)]
        assert "buttons_total" in codes(check(content=with_content(buttons=many)))
        long = check(content=with_content(buttons=[{"type": "QUICK_REPLY", "text": "x" * 26}]))
        assert "button_text_length" in codes(long)
        assert "button_text" in codes(
            check(content=with_content(buttons=[{"type": "QUICK_REPLY", "text": ""}]))
        )
        three_urls = [{"type": "URL", "text": "Open", "url": "https://example.com"}] * 3
        assert "url_count" in codes(check(content=with_content(buttons=three_urls)))
        assert "phone_count" in codes(
            check(
                content=with_content(
                    buttons=[
                        {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+919876543210"}
                    ]
                    * 2
                )
            )
        )
        assert "button_type" in codes(
            check(content=with_content(buttons=[{"type": "COPY_CODE", "text": "x"}]))
        )

    def test_url_buttons(self) -> None:
        assert "url_required" in codes(
            check(content=with_content(buttons=[{"type": "URL", "text": "Open"}]))
        )
        assert "url_format" in codes(
            check(content=with_content(buttons=[{"type": "URL", "text": "Open", "url": "ftp://x"}]))
        )
        missing = check(
            content=with_content(
                buttons=[{"type": "URL", "text": "Track", "url": "https://x.com/t/{{1}}"}]
            )
        )
        assert "url_example_missing" in codes(missing)
        good = check(
            content=with_content(
                buttons=[
                    {
                        "type": "URL",
                        "text": "Track",
                        "url": "https://x.com/t/{{1}}",
                        "url_example": "A1",
                    }
                ]
            )
        )
        assert good.ok
        button = good.components[-1]["buttons"][0]
        assert button["example"] == ["A1"] and button["url"].endswith("{{1}}")

    def test_buttons_are_ordered_quick_replies_first_as_meta_requires(self) -> None:
        v = check(
            content=with_content(
                buttons=[
                    {"type": "URL", "text": "Open", "url": "https://example.com"},
                    {"type": "QUICK_REPLY", "text": "Yes"},
                    {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+919876543210"},
                    {"type": "QUICK_REPLY", "text": "No"},
                ]
            )
        )
        assert v.ok
        kinds = [b["type"] for b in v.components[-1]["buttons"]]
        assert kinds == ["QUICK_REPLY", "QUICK_REPLY", "URL", "PHONE_NUMBER"]
        assert any(w.code == "desktop_view" for w in v.warnings)

    def test_more_than_three_buttons_warns(self) -> None:
        four = [{"type": "QUICK_REPLY", "text": f"B{i}"} for i in range(4)]
        assert any(
            w.code == "many_buttons" for w in check(content=with_content(buttons=four)).warnings
        )

    def test_unknown_fields_are_dropped_never_forwarded(self) -> None:
        v = check(content={**CONTENT, "components": [{"type": "EVIL"}], "text": "x"})
        assert all(c["type"] in {"BODY", "HEADER", "FOOTER", "BUTTONS"} for c in v.components)

    def test_name_taken_and_duplicate_wording(self) -> None:
        existing = [{"name": "order_shipped", "language": "en_US", "body": BODY, "footer": ""}]
        assert "name_taken" in codes(check(existing=existing))
        other = check(name="other_name", existing=existing)
        assert "duplicate_content" in codes(other)
        assert "name_taken" not in codes(other)
        different_footer = check(
            name="other_name", content=with_content(footer="Thanks"), existing=existing
        )
        assert "duplicate_content" not in codes(different_footer)

    def test_account_limit_is_advisory_only_when_not_verified(self) -> None:
        assert any(
            w.code == "template_limit"
            for w in check(template_count=250, business_verified=False).warnings
        )
        assert any(
            w.code == "template_limit"
            for w in check(template_count=250, business_verified=None).warnings
        )
        assert not any(
            w.code == "template_limit"
            for w in check(template_count=250, business_verified=True).warnings
        )
        assert not any(w.code == "template_limit" for w in check(template_count=10).warnings)

    def test_hash_changes_with_any_content_change(self) -> None:
        a = studio.hash_for("n", "en_US", "UTILITY", "POSITIONAL", check())
        b = studio.hash_for(
            "n", "en_US", "UTILITY", "POSITIONAL", check(content=with_content(footer="x"))
        )
        c = studio.hash_for("n2", "en_US", "UTILITY", "POSITIONAL", check())
        assert len({a, b, c}) == 3


# ---------------------------------------------------------------- API helpers


def payload(waba: Any, **over: Any) -> dict[str, Any]:
    body = {
        "business_account_id": str(waba.pk),
        "name": "order_shipped",
        "language": "en_US",
        "category": "UTILITY",
        "parameter_format": "POSITIONAL",
        "content": CONTENT,
    }
    body.update(over)
    return body


def make_draft(api: Any, waba: Any, **over: Any) -> dict[str, Any]:
    response = api.post("/api/v1/templates/drafts/", payload(waba, **over), format="json")
    assert response.status_code == 201, response.content
    return response.json()


def submit(api: Any, draft: dict[str, Any], **over: Any) -> Any:
    body = {"confirm": True, "content_hash": draft["content_hash"], **over}
    return api.post(f"/api/v1/templates/drafts/{draft['id']}/submit/", body, format="json")


CREATE_ROUTE = "message_templates"


def route_create(fake_graph: FakeGraph, waba: Any, *responders: Any) -> None:
    fake_graph.on("POST", f"/{waba.external_id}/message_templates", *responders)


PENDING_OK = {"id": "tpl-111", "status": "PENDING", "category": "UTILITY"}


# ---------------------------------------------------------------- permissions + isolation


class TestAccess:
    def test_unauthenticated_is_refused(self) -> None:
        for method, url in (
            ("get", "/api/v1/templates/studio/options/"),
            ("post", "/api/v1/templates/studio/validate/"),
            ("get", "/api/v1/templates/drafts/"),
            ("post", "/api/v1/templates/drafts/"),
        ):
            assert getattr(APIClient(), method)(url).status_code in (401, 403)

    def test_an_agent_cannot_write_or_submit_but_can_read(
        self, organization: Any, waba: Any, owner_api: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba)
        agent = api_client_for(factories.make_member(organization, "agent"), organization)
        assert (
            agent.post(
                "/api/v1/templates/drafts/", payload(waba, name="x_two"), format="json"
            ).status_code
            == 403
        )
        assert (
            agent.post(
                "/api/v1/templates/studio/validate/", payload(waba), format="json"
            ).status_code
            == 403
        )
        assert submit(agent, draft).status_code == 403
        assert (
            agent.patch(
                f"/api/v1/templates/drafts/{draft['id']}/", {"name": "n"}, format="json"
            ).status_code
            == 403
        )
        assert agent.delete(f"/api/v1/templates/drafts/{draft['id']}/").status_code == 403
        assert agent.post(f"/api/v1/templates/drafts/{draft['id']}/check/").status_code == 403
        assert agent.get("/api/v1/templates/studio/options/").status_code == 200
        assert agent.get(f"/api/v1/templates/drafts/{draft['id']}/").status_code == 200
        assert not fake_graph.calls

    def test_an_api_key_without_the_manage_scope_cannot_submit(
        self, owner: Any, organization: Any, waba: Any, owner_api: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba)
        _, raw = create_api_key(
            organization=organization, created_by=owner, name="k", scopes=["templates.view"]
        )
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw}", HTTP_X_ORGANIZATION_ID=str(organization.pk)
        )
        assert submit(client, draft).status_code == 403
        assert not fake_graph.calls

    def test_another_organizations_account_and_drafts_are_404(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        other_owner = factories.make_user()
        other_org = factories.make_organization(owner=other_owner)
        theirs = factories.make_waba(other_org)
        assert (
            owner_api.post("/api/v1/templates/drafts/", payload(theirs), format="json").status_code
            == 404
        )
        assert (
            owner_api.post(
                "/api/v1/templates/studio/validate/", payload(theirs), format="json"
            ).status_code
            == 404
        )

        other_api = api_client_for(other_owner, other_org)
        draft = make_draft(other_api, theirs)
        assert owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").status_code == 404
        assert submit(owner_api, draft).status_code == 404
        assert owner_api.delete(f"/api/v1/templates/drafts/{draft['id']}/").status_code == 404
        assert draft["id"] not in json.dumps(owner_api.get("/api/v1/templates/drafts/").json())
        assert not fake_graph.calls

    def test_a_member_limited_to_one_business_never_sees_the_others(
        self, organization: Any
    ) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        kiosk_waba = factories.make_waba(organization, business=kiosk)
        eps_waba = factories.make_waba(organization, business=eps)
        owner_like = api_client_for(factories.make_member(organization, "admin"), organization)
        eps_draft = make_draft(owner_like, eps_waba)
        admin = factories.make_member(organization, "admin", businesses=[kiosk])
        api = api_client_for(admin, organization)
        assert (
            api.post(
                "/api/v1/templates/drafts/", payload(eps_waba, name="n2"), format="json"
            ).status_code
            == 404
        )
        assert api.get(f"/api/v1/templates/drafts/{eps_draft['id']}/").status_code == 404
        assert submit(api, eps_draft).status_code == 404
        mine = make_draft(api, kiosk_waba)
        assert [d["id"] for d in api.get("/api/v1/templates/drafts/").json()["results"]] == [
            mine["id"]
        ]

    def test_template_guidance_and_copy_are_tenant_scoped(self, owner_api: Any, waba: Any) -> None:
        other_org = factories.make_organization()
        theirs = factories.make_template(factories.make_waba(other_org))
        assert owner_api.get(f"/api/v1/templates/{theirs.pk}/guidance/").status_code == 404
        assert owner_api.post(f"/api/v1/templates/{theirs.pk}/copy-to-draft/").status_code == 404


# ---------------------------------------------------------------- drafts


class TestDrafts:
    def test_options_state_what_is_unsupported(self, owner_api: Any) -> None:
        body = owner_api.get("/api/v1/templates/studio/options/").json()
        assert [c["value"] for c in body["categories"] if c["supported"]] == [
            "UTILITY",
            "MARKETING",
        ]
        assert body["limits"]["body"] == 1024 and body["limits"]["creations_per_hour"] == 100
        assert any("Authentication" in u for u in body["unsupported"])
        assert {x["value"] for x in body["languages"]} >= {"en_US", "hi"}

    def test_validate_checks_without_saving_or_sending(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        body = owner_api.post(
            "/api/v1/templates/studio/validate/",
            payload(waba, content=with_content(body="{{1}} bad", examples={})),
            format="json",
        ).json()
        assert body["ok"] is False
        assert {e["code"] for e in body["errors"]} >= {"variable_at_edge", "example_missing"}
        assert TemplateDraft.objects.count() == 0 and not fake_graph.calls

    def test_saving_a_draft_is_local_only_and_never_calls_meta(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba)
        assert draft["status"] == "draft" and draft["guidance"]["state"] == "draft"
        assert draft["guidance"]["can_send"] is False
        assert draft["checks"]["ok"] is True and draft["can_submit"] is True
        assert MessageTemplate.objects.count() == 0 and not fake_graph.calls

    def test_an_invalid_draft_can_be_saved_but_not_submitted(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba, content=with_content(body="{{1}} bad", examples={}))
        assert draft["can_submit"] is False and draft["checks"]["errors"]
        response = submit(owner_api, draft)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "TEMPLATE_INVALID"
        assert not fake_graph.calls

    def test_edit_and_delete_a_draft(self, owner_api: Any, waba: Any) -> None:
        draft = make_draft(owner_api, waba)
        edited = owner_api.patch(
            f"/api/v1/templates/drafts/{draft['id']}/",
            {"content": with_content(footer="Thanks")},
            format="json",
        ).json()
        assert (
            edited["content"]["footer"] == "Thanks"
            and edited["content_hash"] != draft["content_hash"]
        )
        assert owner_api.delete(f"/api/v1/templates/drafts/{draft['id']}/").status_code == 204
        assert TemplateDraft.objects.count() == 0

    def test_the_same_name_and_language_cannot_be_drafted_twice(
        self, owner_api: Any, waba: Any
    ) -> None:
        make_draft(owner_api, waba)
        again = owner_api.post("/api/v1/templates/drafts/", payload(waba), format="json")
        assert again.status_code == 409 and again.json()["error"]["code"] == "DRAFT_CONFLICT"

    def test_copying_a_rejected_template_makes_a_corrected_draft_under_a_new_name(
        self, owner_api: Any, waba: Any
    ) -> None:
        template = MessageTemplate.objects.create(
            business_account=waba,
            external_id="9",
            name="order_shipped",
            language="en_US",
            category="UTILITY",
            status="REJECTED",
            rejected_reason="INVALID_FORMAT",
            components=[
                {"type": "BODY", "text": "{{1}} order", "example": {"body_text": [["Asha"]]}},
                {"type": "FOOTER", "text": "Thanks"},
            ],
        )
        response = owner_api.post(f"/api/v1/templates/{template.pk}/copy-to-draft/")
        assert response.status_code == 201, response.content
        draft = response.json()
        assert draft["name"] == "order_shipped_v2" and draft["status"] == "draft"
        assert draft["content"]["body"] == "{{1}} order" and draft["content"]["examples"] == {
            "1": "Asha"
        }
        # the copy keeps the rejected wording, so it still fails the studio's own check
        assert any(e["code"] == "variable_at_edge" for e in draft["checks"]["errors"])
        assert (
            owner_api.post(f"/api/v1/templates/{template.pk}/copy-to-draft/").json()["name"]
            == "order_shipped_v3"
        )

    def test_a_template_with_a_media_header_cannot_be_copied(
        self, owner_api: Any, waba: Any
    ) -> None:
        template = MessageTemplate.objects.create(
            business_account=waba,
            external_id="10",
            name="with_image",
            language="en_US",
            category="MARKETING",
            status="REJECTED",
            components=[
                {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["h"]}},
                {"type": "BODY", "text": "Hello there everyone"},
            ],
        )
        response = owner_api.post(f"/api/v1/templates/{template.pk}/copy-to-draft/")
        assert response.status_code == 400 and "media header" in response.json()["error"]["message"]


# ---------------------------------------------------------------- submit


class TestSubmit:
    def test_nothing_is_sent_without_explicit_confirmation(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba)
        assert submit(owner_api, draft, confirm=False).status_code in (400, 409)
        bad = owner_api.post(
            f"/api/v1/templates/drafts/{draft['id']}/submit/",
            {"content_hash": draft["content_hash"]},
            format="json",
        )
        assert bad.status_code == 400
        assert TemplateDraft.objects.get(pk=draft["id"]).status == "draft" and not fake_graph.calls

    def test_the_content_hash_must_match_what_was_reviewed(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba)
        owner_api.patch(
            f"/api/v1/templates/drafts/{draft['id']}/",
            {"content": with_content(footer="changed after review")},
            format="json",
        )
        response = submit(owner_api, draft)  # stale hash from before the edit
        assert (
            response.status_code == 409
            and "changed since you reviewed" in response.json()["error"]["message"]
        )
        assert not fake_graph.calls

    def test_a_confirmed_submit_sends_the_exact_components_and_records_a_pending_status(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        route_create(fake_graph, waba, PENDING_OK)
        draft = make_draft(owner_api, waba)

        response = submit(owner_api, draft)

        assert response.status_code == 202, response.content
        body = response.json()
        assert body["status"] == "submitted" and "NOT an approval" in body["note"]
        assert (
            body["guidance"]["state"] == "pending" and body["guidance"]["meta_status"] == "PENDING"
        )
        assert (
            body["guidance"]["can_send"] is False and body["guidance"]["verified_by_meta"] is False
        )
        [sent] = fake_graph.json_bodies("POST", "/message_templates")
        assert sent["name"] == "order_shipped" and sent["language"] == "en_US"
        assert sent["category"] == "UTILITY" and sent["parameter_format"] == "POSITIONAL"
        assert sent["components"][0]["example"] == {"body_text": [["Asha", "ORD-1042"]]}
        template = MessageTemplate.objects.get(name="order_shipped")
        assert template.status == "PENDING" and not template.is_sendable

    def test_a_second_submit_is_refused_and_sends_nothing(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        route_create(fake_graph, waba, PENDING_OK)
        draft = make_draft(owner_api, waba)
        assert submit(owner_api, draft).status_code == 202
        again = submit(owner_api, draft)
        assert (
            again.status_code == 409 and "already sent to Meta" in again.json()["error"]["message"]
        )
        assert len(fake_graph.calls) == 1 and MessageTemplate.objects.count() == 1

    def test_a_concurrent_submit_that_finds_it_submitting_is_refused(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba)
        TemplateDraft.objects.filter(pk=draft["id"]).update(
            status="submitting", last_attempt_at=timezone.now()
        )
        response = submit(owner_api, draft)
        assert (
            response.status_code == 409
            and "already being sent" in response.json()["error"]["message"]
        )
        assert not fake_graph.calls

    def test_an_existing_template_with_the_same_name_blocks_the_submit(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba)
        MessageTemplate.objects.create(
            business_account=waba,
            external_id="5",
            name="order_shipped",
            language="en_US",
            category="UTILITY",
            status="APPROVED",
            components=[{"type": "BODY", "text": "Other words entirely"}],
        )
        response = submit(owner_api, draft)
        assert response.status_code == 422
        assert any(i["code"] == "name_taken" for i in response.json()["error"]["details"]["issues"])
        assert not fake_graph.calls

    def test_the_hourly_creation_limit_is_enforced(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(studio, "CREATIONS_PER_WABA_PER_HOUR", 1)
        route_create(fake_graph, waba, PENDING_OK, {"id": "tpl-222", "status": "PENDING"})
        first = make_draft(owner_api, waba)
        assert submit(owner_api, first).status_code == 202
        second = make_draft(
            owner_api, waba, name="another_one", content=with_content(footer="Different")
        )
        response = submit(owner_api, second)
        assert (
            response.status_code == 409
            and "per account per hour" in response.json()["error"]["message"]
        )
        assert len(fake_graph.calls) == 1

    def test_meta_refusing_returns_its_reason_and_leaves_an_editable_draft(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        route_create(
            fake_graph,
            waba,
            graph_error(400, 100, "Invalid parameter: body_text examples"),
            PENDING_OK,
        )
        draft = make_draft(owner_api, waba)
        response = submit(owner_api, draft)
        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "TEMPLATE_SUBMIT_FAILED" and "Invalid parameter" in error["message"]
        stored = TemplateDraft.objects.get(pk=draft["id"])
        assert stored.status == "draft" and "Invalid parameter" in stored.last_error
        assert MessageTemplate.objects.count() == 0
        # the person can fix and try again
        retry = owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").json()
        assert submit(owner_api, retry).status_code == 202

    def test_an_unknown_outcome_blocks_resubmission_until_a_sync_settles_it(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        import httpx

        route_create(fake_graph, waba, raise_exc(httpx.ReadTimeout("timed out")))
        draft = make_draft(owner_api, waba)
        response = submit(owner_api, draft)
        assert (
            response.status_code == 502
            and response.json()["error"]["details"]["outcome_unknown"] is True
        )
        stored = TemplateDraft.objects.get(pk=draft["id"])
        assert stored.status == "unconfirmed" and MessageTemplate.objects.count() == 0

        blocked = submit(owner_api, draft)
        assert blocked.status_code == 409 and "not sure" in blocked.json()["error"]["message"]
        assert len(fake_graph.calls) == 1

        # no sync since the attempt: still unconfirmed, not guessed
        assert (
            owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").json()["status"]
            == "unconfirmed"
        )

        # a sync completed after the attempt and Meta has nothing -> back to a draft
        waba.templates_last_synced_at = timezone.now() + timedelta(seconds=5)
        waba.save()
        settled = owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").json()
        assert settled["status"] == "draft" and "did not receive" in settled["last_error"]

    def test_a_sync_that_finds_the_template_settles_an_unknown_outcome_as_submitted(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        import httpx

        route_create(fake_graph, waba, raise_exc(httpx.ReadTimeout("timed out")))
        draft = make_draft(owner_api, waba)
        submit(owner_api, draft)
        template = MessageTemplate.objects.create(
            business_account=waba,
            external_id="77",
            name="order_shipped",
            language="en_US",
            category="UTILITY",
            status="PENDING",
            components=[{"type": "BODY", "text": BODY}],
        )
        waba.templates_last_synced_at = timezone.now() + timedelta(seconds=5)
        waba.save()
        settled = owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").json()
        assert settled["status"] == "submitted" and settled["template_id"] == str(template.pk)

    def test_a_disconnected_account_fails_plainly_without_a_provider_call(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        draft = make_draft(owner_api, waba)
        waba.credential.secret_reference = "env:NO_SUCH_TOKEN_VARIABLE"
        waba.credential.save()
        response = submit(owner_api, draft)
        assert response.status_code == 502
        assert TemplateDraft.objects.get(pk=draft["id"]).status == "draft"
        assert not fake_graph.calls


# ---------------------------------------------------------------- status honesty


class TestStatusHonesty:
    def synced(self, waba: Any, status: str, **over: Any) -> MessageTemplate:
        return MessageTemplate.objects.create(
            business_account=waba,
            external_id="42",
            name="from_meta",
            language="en_US",
            category="UTILITY",
            status=status,
            components=[{"type": "BODY", "text": "Hello there"}],
            last_synced_at=timezone.now(),
            **over,
        )

    @pytest.mark.parametrize(
        ("meta_status", "state"),
        [
            ("APPROVED", "approved"),
            ("PENDING", "pending"),
            ("PENDING_REVIEW", "pending"),
            ("REJECTED", "rejected"),
            ("PAUSED", "paused"),
            ("DISABLED", "disabled"),
            ("SOMETHING_NEW", "unknown"),
            ("IN_APPEAL", "unknown"),
        ],
    )
    def test_each_meta_status_maps_to_one_plain_state(
        self, owner_api: Any, waba: Any, meta_status: str, state: str
    ) -> None:
        template = self.synced(waba, meta_status)
        body = owner_api.get(f"/api/v1/templates/{template.pk}/guidance/").json()
        assert body["state"] == state and body["meta_status"] == meta_status
        assert body["can_send"] is (state == "approved")
        assert body["explanation"]

    def test_unknown_statuses_are_shown_verbatim_and_not_sendable(
        self, owner_api: Any, waba: Any
    ) -> None:
        template = self.synced(waba, "SOMETHING_NEW")
        body = owner_api.get(f"/api/v1/templates/{template.pk}/guidance/").json()
        assert body["meta_status"] == "SOMETHING_NEW" and body["can_send"] is False
        assert "does not recognise" in body["explanation"]

    def test_a_create_response_saying_approved_is_not_trusted_until_meta_confirms_later(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        route_create(fake_graph, waba, {"id": "tpl-9", "status": "APPROVED", "category": "UTILITY"})
        draft = make_draft(owner_api, waba)
        response = submit(owner_api, draft).json()
        assert response["guidance"]["state"] == "approved"
        assert response["guidance"]["verified_by_meta"] is False
        assert response["guidance"]["can_send"] is False  # not sendable on the HTTP response alone

        template = MessageTemplate.objects.get(name="order_shipped")
        template.last_synced_at = timezone.now() + timedelta(seconds=5)  # a later sync confirms it
        template.save()
        later = owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").json()
        assert (
            later["guidance"]["verified_by_meta"] is True and later["guidance"]["can_send"] is True
        )

    def test_a_status_webhook_after_the_submit_confirms_it_too(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        route_create(fake_graph, waba, PENDING_OK)
        draft = make_draft(owner_api, waba)
        submit(owner_api, draft)
        template = MessageTemplate.objects.get(name="order_shipped")
        template.status = "APPROVED"
        template.last_status_event_at = timezone.now() + timedelta(seconds=5)
        template.save()
        body = owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").json()["guidance"]
        assert body["state"] == "approved" and body["verified_by_meta"] and body["can_send"]

    def test_a_rejection_shows_metas_real_reason_with_plain_help(
        self, owner_api: Any, waba: Any
    ) -> None:
        template = self.synced(waba, "REJECTED", rejected_reason="INVALID_FORMAT")
        body = owner_api.get(f"/api/v1/templates/{template.pk}/guidance/").json()
        assert body["state"] == "rejected" and body["can_fix_as_new"] is True
        assert body["reason"]["code"] == "INVALID_FORMAT"
        assert "variable" in body["reason"]["meaning"].lower() and body["reason"]["how_to_fix"]
        assert any("WhatsApp Manager" in link["label"] for link in body["links"])

    def test_an_unrecognised_rejection_reason_is_shown_verbatim_with_general_steps(
        self, owner_api: Any, waba: Any
    ) -> None:
        template = self.synced(waba, "REJECTED", rejected_reason="BRAND_NEW_REASON")
        reason = owner_api.get(f"/api/v1/templates/{template.pk}/guidance/").json()["reason"]
        assert reason["code"] == "BRAND_NEW_REASON"
        assert "recognises" in reason["meaning"] and "variables" in reason["how_to_fix"]

    def test_pending_says_meta_decides_not_us(self, owner_api: Any, waba: Any) -> None:
        template = self.synced(waba, "PENDING")
        body = owner_api.get(f"/api/v1/templates/{template.pk}/guidance/").json()
        assert "24 hours" in body["explanation"] and body["can_send"] is False

    def test_state_helper_for_a_draft_without_a_template(self) -> None:
        assert guidance.state_of(None, None) == "draft"


# ---------------------------------------------------------------- audit


class TestAudit:
    def test_the_history_never_contains_message_text_or_examples(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        route_create(fake_graph, waba, PENDING_OK)
        draft = make_draft(owner_api, waba, content=with_content(footer="Super secret footer"))
        owner_api.patch(
            f"/api/v1/templates/drafts/{draft['id']}/",
            {"content": with_content(footer="Super secret footer")},
            format="json",
        )
        fresh = owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").json()
        submit(owner_api, fresh)

        actions_logged = list(AuditLog.objects.values_list("action", flat=True))
        for expected in ("template.draft_saved", "template.submit_requested", "template.submitted"):
            assert expected in actions_logged
        everything = json.dumps(
            list(AuditLog.objects.values_list("metadata", flat=True)), default=str
        )
        for secret in ("Super secret footer", "ORD-1042", "Asha", "your order"):
            assert secret not in everything
        submitted = AuditLog.objects.get(action="template.submitted")
        assert submitted.actor is not None and submitted.metadata["meta_status"] == "PENDING"

    def test_a_failed_submit_is_audited_with_the_error_code_only(
        self, owner_api: Any, waba: Any, fake_graph: FakeGraph
    ) -> None:
        route_create(
            fake_graph,
            waba,
            graph_error(400, 100, "Invalid parameter: footer text is Super secret footer"),
        )
        draft = make_draft(owner_api, waba)
        submit(owner_api, draft)
        failed = AuditLog.objects.get(action="template.submit_failed")
        assert failed.metadata["error_code"] and "outcome_unknown" in failed.metadata
        assert "Super secret" not in json.dumps(failed.metadata)

    def test_deleting_a_draft_is_audited(self, owner_api: Any, waba: Any) -> None:
        draft = make_draft(owner_api, waba)
        owner_api.delete(f"/api/v1/templates/drafts/{draft['id']}/")
        assert AuditLog.objects.filter(action="template.draft_deleted").count() == 1


def test_a_submitted_draft_cannot_be_edited_or_deleted(
    owner_api: Any, waba: Any, fake_graph: FakeGraph
) -> None:
    route_create(fake_graph, waba, PENDING_OK)
    draft = make_draft(owner_api, waba)
    submit(owner_api, draft)
    assert (
        owner_api.patch(
            f"/api/v1/templates/drafts/{draft['id']}/", {"name": "x"}, format="json"
        ).status_code
        == 409
    )
    assert owner_api.delete(f"/api/v1/templates/drafts/{draft['id']}/").status_code == 409
    shown = owner_api.get(f"/api/v1/templates/drafts/{draft['id']}/").json()
    assert (
        shown["checks"]["ok"] is True
    )  # a sent draft is not flagged as a naming clash with itself
