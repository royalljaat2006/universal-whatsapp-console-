"""Domain services: contacts, WABA connection/sync, templates, pricing, audit."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.billing.models import MessageCost, PricingRule
from apps.billing.pricing import PricingRuleNotFoundError, quote
from apps.contacts.models import ContactIdentity
from apps.contacts.services import (
    ContactIdentifiers,
    InvalidIdentifierError,
    normalize_phone,
    resolve_contact,
)
from apps.organizations.tenancy import CrossTenantReferenceError, ensure_same_organization
from apps.templates.models import MessageTemplate
from apps.templates.services import apply_template_status_event, sync_templates
from apps.whatsapp.models import (
    ConnectionState,
    SyncState,
    WhatsAppBusinessAccount,
    WhatsAppPhoneNumber,
)
from apps.whatsapp.services import (
    connect_business_account,
    create_credential_reference,
    register_business_account,
    sync_phone_numbers,
    verify_connection,
)
from core.exceptions import ConfigurationRequiredError, ConflictError, NotFoundError
from core.secrets import SecretReferenceError
from providers.whatsapp.errors import ProviderAuthError, ProviderError
from providers.whatsapp.types import TemplateStatusEvent
from tests import factories
from tests.fakes import FakeGraph, graph_error

pytestmark = pytest.mark.django_db


class TestContacts:
    def test_phone_normalisation(self) -> None:
        assert normalize_phone("+91 98765-43210") == "919876543210"
        for bad in ("0123", "abc", "+0919876543210"):
            with pytest.raises(InvalidIdentifierError):
                normalize_phone(bad)

    def test_bsuid_learned_later_links_to_same_contact(self, organization: Any) -> None:
        first = resolve_contact(
            factories.default_business(organization), ContactIdentifiers(phone_number="16505551234")
        )
        second = resolve_contact(
            factories.default_business(organization),
            ContactIdentifiers(phone_number="16505551234", bsuid="US.1", bsuid_scope="p1"),
        )
        third = resolve_contact(
            factories.default_business(organization),
            ContactIdentifiers(bsuid="US.1", bsuid_scope="p1"),
        )
        assert first.pk == second.pk == third.pk
        assert ContactIdentity.objects.filter(contact=first).count() == 2

    def test_same_bsuid_in_other_portfolio_is_other_contact(self, organization: Any) -> None:
        a = resolve_contact(
            factories.default_business(organization),
            ContactIdentifiers(bsuid="US.1", bsuid_scope="p1"),
        )
        b = resolve_contact(
            factories.default_business(organization),
            ContactIdentifiers(bsuid="US.1", bsuid_scope="p2"),
        )
        assert a.pk != b.pk

    def test_contacts_are_per_tenant(self, organization: Any) -> None:
        other = factories.make_organization()
        a = resolve_contact(
            factories.default_business(organization), ContactIdentifiers(phone_number="16505551234")
        )
        b = resolve_contact(
            factories.default_business(other), ContactIdentifiers(phone_number="16505551234")
        )
        assert a.pk != b.pk

    def test_cross_tenant_guard(self, organization: Any) -> None:
        other = factories.make_organization()
        contact = resolve_contact(
            factories.default_business(other), ContactIdentifiers(phone_number="16505551234")
        )
        phone = factories.make_phone_number(factories.make_waba(organization))
        with pytest.raises(CrossTenantReferenceError):
            ensure_same_organization(phone, contact)


WABA_INFO = {
    "id": "555",
    "name": "Kiosk WABA",
    "timezone_id": "71",
    "message_template_namespace": "ns",
    "account_review_status": "APPROVED",
    "business_verification_status": "VERIFIED",
    "country": "IN",
    "ownership_type": "SELF",
}


def _connect_routes(fake_graph: FakeGraph, waba: str = "555", subscribed: bool = False) -> None:
    fake_graph.on("GET", f"/{waba}", {**WABA_INFO, "id": waba})
    fake_graph.on(
        "GET",
        f"/{waba}/subscribed_apps",
        {"data": [{"whatsapp_business_api_data": {}}]} if subscribed else {"data": []},
    )
    fake_graph.on("POST", f"/{waba}/subscribed_apps", {"success": True})
    fake_graph.on(
        "GET",
        f"/{waba}/phone_numbers",
        {
            "data": [
                {
                    "id": "p1",
                    "display_phone_number": "+91 1",
                    "status": "CONNECTED",
                    "quality_rating": "GREEN",
                    "messaging_limit_tier": "TIER_1K",
                },
                {"id": "p2", "display_phone_number": "+91 2", "status": "PENDING"},
            ]
        },
    )


def _credential(organization: Any) -> Any:
    return create_credential_reference(
        organization=organization, label="t", secret_reference=f"env:{factories.TEST_TOKEN_ENV}"
    )


class TestWabaConnection:
    def test_connect_verifies_subscribes_and_syncs(
        self, organization: Any, fake_graph: FakeGraph
    ) -> None:
        _connect_routes(fake_graph)
        kiosk = factories.make_business(organization, "Kiosk")
        account = connect_business_account(
            organization=organization,
            business=kiosk,
            external_id="555",
            credential=_credential(organization),
        )
        assert account.business_id == kiosk.pk
        assert account.connection_state == ConnectionState.CONNECTED
        assert account.sync_state == SyncState.SUCCEEDED
        assert account.name == "Kiosk WABA"
        assert account.business_verification_status == "VERIFIED"
        assert account.webhooks_subscribed_at is not None
        numbers = {n.external_id: n for n in account.phone_numbers.all()}
        assert numbers["p1"].status == WhatsAppPhoneNumber.Status.CONNECTED
        assert numbers["p1"].business_id == kiosk.pk  # denormalised from the WABA
        assert numbers["p1"].messaging_limit_tier == "TIER_1K"
        assert numbers["p2"].status == WhatsAppPhoneNumber.Status.PENDING
        called = [(c.method, c.url.path) for c in fake_graph.calls]
        assert ("POST", "/v26.0/555/subscribed_apps") in called
        assert fake_graph.calls[0].headers["Authorization"] == f"Bearer {factories.TEST_TOKEN}"
        actions = set(AuditLog.objects.values_list("action", flat=True))
        assert {
            "credential.created",
            "whatsapp.waba_connected",
            "whatsapp.waba_verified",
            "whatsapp.phone_numbers_synced",
        } <= actions
        # Secrets never reach the audit log.
        assert factories.TEST_TOKEN not in str(
            list(AuditLog.objects.values_list("metadata", flat=True))
        )

    def test_already_subscribed_is_not_resubscribed(
        self, organization: Any, fake_graph: FakeGraph
    ) -> None:
        _connect_routes(fake_graph, subscribed=True)
        connect_business_account(
            organization=organization,
            business=factories.make_business(organization),
            external_id="555",
            credential=_credential(organization),
        )
        assert not [c for c in fake_graph.calls if c.method == "POST"]

    def test_invalid_token_marks_connection_error(
        self, organization: Any, fake_graph: FakeGraph
    ) -> None:
        fake_graph.on("GET", "/555", graph_error(401, 190, "Session has expired"))
        with pytest.raises(ProviderAuthError):
            connect_business_account(
                organization=organization,
                business=factories.make_business(organization),
                external_id="555",
                credential=_credential(organization),
            )
        account = WhatsAppBusinessAccount.objects.get(external_id="555")
        assert account.connection_state == ConnectionState.ERROR
        assert account.connection_error_code == "PROVIDER_AUTH_FAILED"
        assert "expired" in account.connection_error

    def test_missing_secret_marks_connection_error(
        self, organization: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(factories.TEST_TOKEN_ENV)
        account = register_business_account(
            organization=organization,
            business=factories.make_business(organization),
            external_id="555",
            credential=_credential(organization),
        )
        with pytest.raises(ConfigurationRequiredError):
            verify_connection(account)
        account.refresh_from_db()
        assert account.connection_state == ConnectionState.ERROR
        assert account.connection_error_code == "CONFIGURATION_REQUIRED"

    def test_waba_owned_by_another_tenant_cannot_be_claimed(self, organization: Any) -> None:
        other = factories.make_organization()
        factories.make_waba(other, external_id="777")
        with pytest.raises(NotFoundError):
            register_business_account(
                organization=organization,
                business=factories.make_business(organization),
                external_id="777",
                credential=_credential(organization),
            )

    def test_waba_cannot_move_between_businesses(self, organization: Any) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        factories.make_waba(organization, external_id="888", business=kiosk)
        with pytest.raises(ConflictError):
            register_business_account(
                organization=organization,
                business=eps,
                external_id="888",
                credential=_credential(organization),
            )

    def test_credential_reference_must_be_allowlisted(self, organization: Any) -> None:
        with pytest.raises(SecretReferenceError):
            create_credential_reference(
                organization=organization, label="x", secret_reference="env:DATABASE_URL"
            )

    def test_sync_preserves_local_fields_and_disconnects_missing(
        self, waba: Any, fake_graph: FakeGraph
    ) -> None:
        kept = factories.make_phone_number(waba, external_id="k1")
        kept.label = "Support line"
        kept.throughput_mps = 20
        kept.save()
        stale = factories.make_phone_number(waba)
        fake_graph.on(
            "GET",
            f"/{waba.external_id}/phone_numbers",
            {"data": [{"id": "k1", "display_phone_number": "+91 9", "status": "FLAGGED"}]},
        )
        sync_phone_numbers(waba)
        kept.refresh_from_db()
        stale.refresh_from_db()
        assert kept.label == "Support line" and kept.throughput_mps == 20
        assert kept.status == WhatsAppPhoneNumber.Status.FLAGGED
        assert stale.status == WhatsAppPhoneNumber.Status.DISCONNECTED

    def test_sync_failure_keeps_existing_numbers(self, waba: Any, fake_graph: FakeGraph) -> None:
        number = factories.make_phone_number(waba)
        fake_graph.on("GET", f"/{waba.external_id}/phone_numbers", graph_error(500, 131016))
        with pytest.raises(ProviderError):
            sync_phone_numbers(waba)
        waba.refresh_from_db()
        number.refresh_from_db()
        assert waba.sync_state == SyncState.FAILED
        assert "PROVIDER_TRANSIENT" in waba.sync_error
        assert number.status == WhatsAppPhoneNumber.Status.CONNECTED

    def test_number_owned_by_other_waba_is_reported_not_stolen(
        self, organization: Any, fake_graph: FakeGraph
    ) -> None:
        first = factories.make_waba(organization)
        owned = factories.make_phone_number(first, external_id="shared")
        second = factories.make_waba(organization)
        fake_graph.on(
            "GET",
            f"/{second.external_id}/phone_numbers",
            {"data": [{"id": "shared", "display_phone_number": "+1", "status": "CONNECTED"}]},
        )
        sync_phone_numbers(second)
        owned.refresh_from_db()
        second.refresh_from_db()
        assert owned.business_account_id == first.pk
        assert "shared" in second.sync_error


class TestTemplates:
    def test_sync_upserts_and_versions(self, waba: Any, fake_graph: FakeGraph) -> None:
        item = {
            "id": "t1",
            "name": "order_update",
            "language": "en",
            "category": "UTILITY",
            "status": "APPROVED",
            "components": [{"type": "BODY", "text": "Hi"}],
        }
        fake_graph.on("GET", f"/{waba.external_id}/message_templates", {"data": [item]})
        assert sync_templates(waba) == 1
        assert sync_templates(waba) == 1  # idempotent
        template = MessageTemplate.objects.get()
        assert template.versions.count() == 1
        assert template.is_sendable

    def test_status_events_ignore_stale_updates(self, waba: Any) -> None:
        template = factories.make_template(waba, status="PENDING")
        now = timezone.now()

        def event(name: str, at: Any) -> TemplateStatusEvent:
            return TemplateStatusEvent(
                waba.external_id, template.external_id, template.name, "en", name, occurred_at=at
            )

        apply_template_status_event(waba, event("APPROVED", now))
        apply_template_status_event(waba, event("PENDING", now - timedelta(minutes=5)))
        template.refresh_from_db()
        assert template.status == "APPROVED"
        assert template.versions.count() == 1


class TestPricing:
    def _rule(self, **kw: Any) -> PricingRule:
        defaults = {
            "provider": "meta",
            "market": "IN",
            "category": "marketing",
            "currency": "INR",
            "rate": Decimal("0.7846"),
            "effective_from": timezone.now() - timedelta(days=30),
        }
        return PricingRule.objects.create(**{**defaults, **kw})

    def test_effective_dated_lookup(self) -> None:
        now = timezone.now()
        old = self._rule(effective_to=now - timedelta(days=1), rate=Decimal("0.70"))
        new = self._rule(effective_from=now - timedelta(days=1), rate=Decimal("0.80"))
        assert (
            quote(provider="meta", market="IN", category="marketing", billable=True, at=now).rule
            == new
        )
        at_past = now - timedelta(days=5)
        assert (
            quote(
                provider="meta", market="in", category="marketing", billable=True, at=at_past
            ).rule
            == old
        )

    def test_tenant_override_and_rest_of_world(self, organization: Any) -> None:
        self._rule(market="*", rate=Decimal("1.0"))
        override = self._rule(organization=organization, rate=Decimal("0.5"))
        now = timezone.now()
        assert quote(
            provider="meta", market="BR", category="marketing", billable=True, at=now
        ).unit_rate == Decimal("1.0")
        result = quote(
            provider="meta",
            market="IN",
            category="marketing",
            billable=True,
            at=now,
            organization_id=organization.pk,
        )
        assert result.rule == override

    def test_non_billable_is_free_and_needs_no_rule(self) -> None:
        result = quote(
            provider="meta", market="IN", category="service", billable=False, at=timezone.now()
        )
        assert result.amount == 0 and result.rule is None

    def test_missing_rule_raises(self) -> None:
        with pytest.raises(PricingRuleNotFoundError):
            quote(
                provider="meta", market="IN", category="utility", billable=True, at=timezone.now()
            )

    def test_message_cost_is_immutable(self, organization: Any, phone: Any) -> None:
        from apps.messaging.models import Message

        factories.open_service_window(phone, "16505551234")
        conversation = phone.conversations.get()
        now = timezone.now()
        message = Message.objects.create(
            organization=organization,
            conversation=conversation,
            phone_number=phone,
            contact=conversation.contact,
            direction="outbound",
            message_type="text",
            status="delivered",
            occurred_at=now,
            status_changed_at=now,
        )
        cost = MessageCost.objects.create(
            organization=organization,
            message=message,
            provider="meta",
            market="IN",
            category="utility",
            billable=True,
            currency="INR",
            unit_rate=Decimal("0.1"),
            amount=Decimal("0.1"),
            priced_at=now,
        )
        cost.amount = Decimal("9")
        with pytest.raises(TypeError):
            cost.save()


def test_audit_log_is_append_only(organization: Any) -> None:
    entry = AuditLog.objects.filter(organization=organization).first()
    assert entry is not None
    with pytest.raises(TypeError):
        entry.save()
    with pytest.raises(TypeError):
        entry.delete()
    with pytest.raises(TypeError):
        AuditLog.objects.filter(pk=entry.pk).update(action="x")
