"""Health rules: each maps ONE stored signal to a state, never inventing health.

Pure tests (unsaved model instances, no database)."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.messaging.services import failure_categories as fc
from apps.whatsapp import health
from apps.whatsapp.models import WhatsAppBusinessAccount, WhatsAppPhoneNumber

NOW = timezone.now()


def _waba(**overrides: Any) -> WhatsAppBusinessAccount:
    values: dict[str, Any] = {
        "connection_state": "connected",
        "webhooks_subscribed_at": NOW,
        "account_review_status": "APPROVED",
        "business_verification_status": "verified",
        "template_sync_state": "succeeded",
        "templates_last_synced_at": NOW,
        "last_verified_at": NOW,
    }
    values.update(overrides)
    return WhatsAppBusinessAccount(**values)


def _phone(**overrides: Any) -> WhatsAppPhoneNumber:
    values: dict[str, Any] = {
        "status": "connected",
        "provider_status": "CONNECTED",
        "quality_rating": "GREEN",
        "name_status": "APPROVED",
        "messaging_limit_tier": "TIER_1K",
        "last_synced_at": NOW,
    }
    values.update(overrides)
    return WhatsAppPhoneNumber(**values)


def _by_code(assessment: health.Assessment) -> dict[str, health.Check]:
    return {c.code: c for c in assessment.checks}


class TestAccount:
    def test_a_fully_healthy_account(self) -> None:
        result = health.assess_account(_waba(), now=NOW)
        assert result.overall == "healthy"
        assert all(c.state == "ok" for c in result.checks)

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("connected", "ok"),
            ("pending", "warning"),
            ("error", "problem"),
            ("disconnected", "problem"),
        ],
    )
    def test_connection_state_mapping(self, state: str, expected: str) -> None:
        assert (
            _by_code(health.assess_account(_waba(connection_state=state), now=NOW))[
                "connection"
            ].state
            == expected
        )

    def test_a_connection_error_shows_the_providers_own_words(self) -> None:
        waba = _waba(connection_state="error", connection_error="Invalid OAuth access token")
        check = _by_code(health.assess_account(waba, now=NOW))["connection"]
        assert "Invalid OAuth access token" in check.detail
        assert check.action == "refresh"

    def test_missing_webhook_subscription_on_a_connected_account_is_a_problem(self) -> None:
        check = _by_code(health.assess_account(_waba(webhooks_subscribed_at=None), now=NOW))[
            "webhooks"
        ]
        assert check.state == "problem" and "replies" in check.detail

    def test_missing_signals_are_unknown_never_ok(self) -> None:
        result = health.assess_account(
            _waba(
                account_review_status="",
                business_verification_status="",
                template_sync_state="never",
                last_verified_at=None,
            ),
            now=NOW,
        )
        checks = _by_code(result)
        for code in ("account_review", "business_verification", "templates_sync", "freshness"):
            assert checks[code].state == "unknown", code

    def test_unrecognised_provider_values_are_shown_verbatim_as_a_warning(self) -> None:
        checks = _by_code(
            health.assess_account(
                _waba(
                    account_review_status="PENDING_SOMETHING",
                    business_verification_status="submitted",
                ),
                now=NOW,
            )
        )
        assert checks["account_review"].state == "warning"
        assert "PENDING_SOMETHING" in checks["account_review"].detail
        assert checks["business_verification"].state == "warning"
        assert "submitted" in checks["business_verification"].detail

    def test_rejected_review_and_restriction_point_to_metas_own_interface(self) -> None:
        checks = _by_code(
            health.assess_account(
                _waba(account_review_status="REJECTED", status="restricted"), now=NOW
            )
        )
        assert (
            checks["account_review"].state == "problem"
            and checks["account_review"].fix_in == "meta"
        )
        assert checks["restricted"].state == "problem" and checks["restricted"].fix_in == "meta"

    def test_stale_data_is_flagged_and_offers_a_refresh(self) -> None:
        old = NOW - timedelta(days=3)
        check = _by_code(health.assess_account(_waba(last_verified_at=old), now=NOW))["freshness"]
        assert check.state == "warning" and check.action == "refresh" and "3 days" in check.detail


class TestNumber:
    def test_a_fully_healthy_number(self) -> None:
        assert health.assess_number(_phone(), now=NOW).overall == "healthy"

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("connected", "ok"),
            ("pending", "warning"),
            ("disconnected", "problem"),
            ("flagged", "problem"),
            ("restricted", "problem"),
        ],
    )
    def test_number_status_mapping(self, status: str, expected: str) -> None:
        assert (
            _by_code(health.assess_number(_phone(status=status), now=NOW))["status"].state
            == expected
        )

    @pytest.mark.parametrize(
        ("rating", "state", "fix_in"),
        [
            ("GREEN", "ok", "none"),
            ("YELLOW", "warning", "meta"),
            ("RED", "problem", "meta"),
            ("UNKNOWN", "unknown", "none"),
            ("", "unknown", "none"),
            ("PURPLE", "warning", "meta"),
        ],
    )
    def test_quality_rating_mapping(self, rating: str, state: str, fix_in: str) -> None:
        check = _by_code(health.assess_number(_phone(quality_rating=rating), now=NOW))["quality"]
        assert (check.state, check.fix_in) == (state, fix_in)
        if rating == "PURPLE":
            assert "PURPLE" in check.detail  # unrecognised -> shown verbatim

    def test_red_quality_says_it_cannot_be_fixed_from_this_console(self) -> None:
        check = _by_code(health.assess_number(_phone(quality_rating="RED"), now=NOW))["quality"]
        assert "WhatsApp Manager" in check.next_step

    @pytest.mark.parametrize(
        ("name", "state"),
        [
            ("APPROVED", "ok"),
            ("AVAILABLE_WITHOUT_REVIEW", "ok"),
            ("PENDING_REVIEW", "warning"),
            ("DECLINED", "warning"),
            ("", "unknown"),
            ("NONE", "unknown"),
        ],
    )
    def test_display_name_mapping(self, name: str, state: str) -> None:
        assert (
            _by_code(health.assess_number(_phone(name_status=name), now=NOW))["display_name"].state
            == state
        )

    @pytest.mark.parametrize(
        ("tier", "state", "fragment"),
        [
            ("TIER_250", "ok", "250 different customers"),
            ("TIER_1K", "ok", "1K different customers"),
            ("TIER_100K", "ok", "100K"),
            ("TIER_UNLIMITED", "ok", "no fixed limit"),
            ("", "unknown", "not reported"),
            ("WEIRD", "unknown", "WEIRD"),
        ],
    )
    def test_messaging_limit_wording_uses_only_what_meta_reported(
        self, tier: str, state: str, fragment: str
    ) -> None:
        check = _by_code(health.assess_number(_phone(messaging_limit_tier=tier), now=NOW))[
            "messaging_limit"
        ]
        assert check.state == state and fragment in check.detail

    def test_a_number_with_no_signals_is_unknown_not_healthy(self) -> None:
        bare = WhatsAppPhoneNumber(status="pending")
        result = health.assess_number(bare, now=NOW)
        assert result.overall != "healthy"
        states = {c.code: c.state for c in result.checks}
        assert states["quality"] == "unknown" and states["freshness"] == "unknown"

    def test_a_recorded_refresh_error_is_surfaced(self) -> None:
        check = _by_code(health.assess_number(_phone(last_error="Graph timeout"), now=NOW))[
            "last_error"
        ]
        assert check.state == "warning" and "Graph timeout" in check.detail


class TestRecentDelivery:
    def _state(
        self, total: int, failed: int, causes: tuple[tuple[str, int], ...] = ()
    ) -> health.Check:
        recent = health.RecentDelivery(total=total, failed=failed, causes=causes)
        return _by_code(health.assess_number(_phone(), now=NOW, recent=recent))["recent_delivery"]

    def test_no_sends_means_nothing_to_measure(self) -> None:
        check = self._state(0, 0)
        assert check.state == "unknown" and check.basis == "console"

    def test_no_failures_is_ok(self) -> None:
        assert self._state(40, 0).state == "ok"

    @pytest.mark.parametrize(
        ("total", "failed", "state"),
        [
            (10, 1, "ok"),  # 10% below the warning rate
            (12, 3, "warning"),  # 25%
            (10, 5, "problem"),  # 50%
            (4, 3, "warning"),  # too few sends for a rate, but 3 absolute failures
            (4, 1, "ok"),
        ],
    )
    def test_thresholds_are_the_documented_console_rules(
        self, total: int, failed: int, state: str
    ) -> None:
        assert self._state(total, failed, ((fc.TEMPORARY, failed),)).state == state

    @pytest.mark.parametrize("category", list(fc.LABELS))
    def test_every_failure_category_has_plain_guidance(self, category: str) -> None:
        check = self._state(10, 6, ((category, 6),))
        assert check.state == "problem"
        assert fc.LABELS[category].lower() in check.detail
        assert check.next_step

    def test_unknown_outcome_is_never_told_to_retry(self) -> None:
        check = self._state(10, 6, ((fc.TIMEOUT, 6),))
        assert "never retried automatically" in check.next_step

    def test_it_says_the_rule_is_ours_not_metas(self) -> None:
        assert self._state(12, 5, ((fc.RATE_LIMIT, 5),)).basis == "console"


class TestSummary:
    def test_problem_counts_are_stated_plainly(self) -> None:
        bad = health.assess_number(_phone(quality_rating="RED", status="flagged"), now=NOW)
        overall, sentence = health.summarize([bad])
        assert overall == "problem" and "2 things need fixing" in sentence

    def test_nothing_connected_is_unknown(self) -> None:
        assert health.summarize([]) == (
            "unknown",
            "No WhatsApp account or number is connected yet.",
        )

    def test_all_good_says_so_without_overclaiming(self) -> None:
        overall, sentence = health.summarize([health.assess_number(_phone(), now=NOW)])
        assert overall == "healthy" and "Meta has reported" in sentence

    def test_as_dict_has_a_stable_shape(self) -> None:
        d = health.as_dict(health.assess_number(_phone(), now=NOW).checks[0])
        assert set(d) == {
            "code",
            "state",
            "title",
            "detail",
            "next_step",
            "fix_in",
            "basis",
            "action",
        }
