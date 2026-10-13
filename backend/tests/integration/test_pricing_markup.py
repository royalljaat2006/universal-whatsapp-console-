"""Pricing adjustment: the platform rate for exempt organizations, platform rate plus the
configured percentage for the rest, never on negotiated rules. The values used here
are test-only; the real ones live in the deployment environment."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.billing import markup
from apps.billing.models import MessageCost, PricingMarket, PricingRule
from apps.billing.pricing import quote
from apps.billing.services import price_message
from apps.messaging.models import Direction, Message
from apps.messaging.services.recovery import estimate_cost
from apps.messaging.state import MessageStatus
from apps.organizations.models import Membership
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)

NOW = timezone.now


@pytest.fixture(autouse=True)
def markup_on(settings: Any) -> None:
    settings.PRICING_MARKUP_PERCENT = Decimal("2")
    settings.PRICING_MARKUP_EXEMPT_EMAIL_DOMAINS = ["exempt.test"]
    cache.clear()


def rule(rate: str = "0.115", **kw: Any) -> PricingRule:
    return PricingRule.objects.create(
        provider="meta",
        market="IN",
        category="utility",
        currency="INR",
        rate=Decimal(rate),
        effective_from=NOW() - timedelta(days=60),
        source="test rate card",
        version="t1",
        **kw,
    )


def org_owned_by(email: str, verified: bool = True) -> Any:
    owner = factories.make_user(email, is_email_verified=verified)
    return factories.make_organization(owner=owner)


def india_market() -> None:
    PricingMarket.objects.create(
        provider="meta",
        code="IN",
        name="India",
        calling_codes=["91"],
        effective_from=NOW() - timedelta(days=60),
        source="test rate card",
    )


class TestWhoIsExempt:
    def test_an_organization_owned_by_a_verified_exempt_address_is_exempt(self) -> None:
        assert markup.is_exempt(org_owned_by("asha@exempt.test").pk) is True

    def test_other_organizations_are_not_exempt(self) -> None:
        assert markup.is_exempt(org_owned_by("asha@example.com").pk) is False

    def test_an_unverified_exempt_address_is_not_exempt(self) -> None:
        assert markup.is_exempt(org_owned_by("asha@exempt.test", verified=False).pk) is False

    @pytest.mark.parametrize(
        "email",
        [
            "x@notexempt.test",
            "x@exempt.test.evil.com",
            "exempt.test@gmail.com",
            "x@mail.exempt.test",
            "x@exempt.tes",
        ],
    )
    def test_lookalike_domains_are_not_exempt(self, email: str) -> None:
        assert markup.is_exempt(org_owned_by(email).pk) is False

    def test_the_domain_match_ignores_case(self) -> None:
        assert markup.is_exempt(org_owned_by("Asha@EXEMPT.TEST").pk) is True

    def test_only_an_active_owner_counts_not_an_admin_or_a_suspended_owner(self) -> None:
        organization = org_owned_by("boss@example.com")
        factories.make_member(
            organization, "admin", user=factories.make_user("a@exempt.test", is_email_verified=True)
        )
        assert markup.is_exempt(organization.pk) is False

        exempt_org = org_owned_by("o@exempt.test")
        Membership.objects.filter(organization=exempt_org, role__key="owner").update(
            status="suspended"
        )
        cache.clear()
        assert markup.is_exempt(exempt_org.pk) is False

    def test_no_domains_configured_means_nobody_is_exempt(self, settings: Any) -> None:
        settings.PRICING_MARKUP_EXEMPT_EMAIL_DOMAINS = []
        assert markup.is_exempt(org_owned_by("asha@exempt.test").pk) is False


class TestRate:
    def test_an_ordinary_organization_pays_two_percent_more(self) -> None:
        organization = org_owned_by("a@example.com")
        assert markup.charged_rate(rule("0.115"), organization.pk) == (
            Decimal("0.117300"),
            Decimal("2"),
        )

    def test_an_exempt_organization_pays_the_platform_rate_exactly(self) -> None:
        organization = org_owned_by("a@exempt.test")
        assert markup.charged_rate(rule("0.115"), organization.pk) == (
            Decimal("0.115000"),
            Decimal("0"),
        )

    def test_rounding_is_half_up_to_six_places(self) -> None:
        organization = org_owned_by("a@example.com")
        assert markup.charged_rate(rule("0.500000"), organization.pk)[0] == Decimal("0.510000")
        assert markup.charged_rate(rule("0.123456"), organization.pk)[0] == Decimal("0.125925")
        assert markup.charged_rate(rule("0.000050"), organization.pk)[0] == Decimal("0.000051")

    def test_zero_percent_switches_the_markup_off(self, settings: Any) -> None:
        settings.PRICING_MARKUP_PERCENT = Decimal("0")
        organization = org_owned_by("a@example.com")
        assert markup.charged_rate(rule("0.115"), organization.pk)[1] == Decimal("0")

    def test_negotiated_rules_are_never_marked_up(self) -> None:
        organization = org_owned_by("a@example.com")
        business = factories.default_business(organization)
        waba = factories.make_waba(organization)
        for kw in (
            {"organization": organization},
            {"business": business},
            {"business_account": waba},
        ):
            negotiated = rule("0.100", **kw)
            assert markup.charged_rate(negotiated, organization.pk) == (
                Decimal("0.100"),
                Decimal("0"),
            )

    def test_explain_records_the_platform_rate_the_percent_and_why(self) -> None:
        ordinary = markup.explain(rule("0.115"), org_owned_by("a@example.com").pk)
        assert Decimal(ordinary["meta_rate"]) == Decimal("0.115")
        assert Decimal(ordinary["charged_rate"]) == Decimal("0.1173")
        assert ordinary["markup_percent"] == "2"
        assert ordinary["markup_reason"] == "platform rate plus markup"
        exempt = markup.explain(rule("0.115"), org_owned_by("a@exempt.test").pk)
        assert exempt["markup_reason"] == "exempt organization"
        assert exempt["markup_percent"] == "0"


class TestQuote:
    def quote_for(self, organization: Any, billable: bool = True) -> Any:
        return quote(
            provider="meta",
            market="IN",
            category="utility",
            billable=billable,
            at=NOW(),
            organization_id=organization.pk,
        )

    def test_the_quote_carries_the_charged_amount_and_the_platform_base(self) -> None:
        rule("0.115")
        q = self.quote_for(org_owned_by("a@example.com"))
        assert q.amount == q.unit_rate == Decimal("0.117300")
        assert q.base_rate == Decimal("0.115000")
        assert q.markup_percent == Decimal("2")

    def test_exempt_quote_is_the_base_rate(self) -> None:
        rule("0.115")
        q = self.quote_for(org_owned_by("a@exempt.test"))
        assert q.amount == Decimal("0.115000") and q.markup_percent == Decimal("0")

    def test_a_non_billable_message_stays_free_with_no_markup(self) -> None:
        rule("0.115")
        q = self.quote_for(org_owned_by("a@example.com"), billable=False)
        assert q.amount == Decimal("0") and q.billable is False


def delivered(phone: Any) -> Message:
    factories.open_service_window(phone, "919876543210")
    conversation = phone.conversations.get()
    now = NOW()
    return Message.objects.create(
        organization=phone.organization,
        conversation=conversation,
        phone_number=phone,
        contact=conversation.contact,
        direction=Direction.OUTBOUND,
        message_type="template",
        status=MessageStatus.DELIVERED,
        occurred_at=now,
        status_changed_at=now,
        delivered_at=now,
        billable=True,
        pricing_category="utility",
    )


class TestPricedMessages:
    @pytest.fixture(autouse=True)
    def card(self) -> None:
        india_market()
        rule("0.115")

    def phone_of(self, email: str) -> Any:
        organization = org_owned_by(email)
        return factories.make_phone_number(factories.make_waba(organization))

    def test_an_ordinary_organization_is_charged_the_marked_up_amount_with_a_snapshot(
        self,
    ) -> None:
        message = delivered(self.phone_of("a@example.com"))
        assert price_message(message.pk) == "priced"
        cost = MessageCost.objects.get(message=message)
        assert cost.amount == cost.unit_rate == Decimal("0.117300")
        snapshot = cost.pricing_snapshot
        assert snapshot["meta_rate"] == "0.115000" and snapshot["unit_rate"] == "0.117300"
        assert snapshot["markup_percent"] == "2"
        assert snapshot["markup_reason"] == "platform rate plus markup"

    def test_an_exempt_organization_is_charged_exactly_the_platform_rate(self) -> None:
        message = delivered(self.phone_of("a@exempt.test"))
        price_message(message.pk)
        cost = MessageCost.objects.get(message=message)
        assert cost.amount == Decimal("0.115000")
        assert cost.pricing_snapshot["markup_percent"] == "0"
        assert cost.pricing_snapshot["markup_reason"] == "exempt organization"

    def test_a_cost_already_priced_is_never_repriced_when_the_markup_changes(
        self, settings: Any
    ) -> None:
        message = delivered(self.phone_of("a@example.com"))
        price_message(message.pk)
        settings.PRICING_MARKUP_PERCENT = Decimal("5")
        cache.clear()
        assert price_message(message.pk) == "priced"
        assert MessageCost.objects.get(message=message).amount == Decimal("0.117300")


class TestEstimates:
    @pytest.fixture(autouse=True)
    def card(self) -> None:
        india_market()
        rule("0.115")

    def estimate(self, organization: Any) -> dict[str, Any]:
        return estimate_cost(
            message_type="template",
            category="UTILITY",
            recipient_phone="919876543210",
            provider="meta",
            organization_id=organization.pk,
            business_id=None,
            business_account_id=None,
        )

    def test_a_preview_estimate_uses_the_same_amount_that_would_be_charged(self) -> None:
        ordinary = self.estimate(org_owned_by("a@example.com"))
        exempt = self.estimate(org_owned_by("a@exempt.test"))
        assert Decimal(ordinary["amount"]) == Decimal("0.117300")
        assert Decimal(exempt["amount"]) == Decimal("0.115000")
