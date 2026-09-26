"""Usage summary: real, currently-countable numbers only (billing.view).
No plan/quota model exists yet — this is the foundation, not enforcement."""

from typing import Any

import pytest

from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db


class TestUsageSummary:
    def test_reports_real_counts(self, owner: Any, organization: Any, phone: Any) -> None:
        factories.make_member(organization, "agent")
        factories.make_contact(phone.business, "919876500001")
        response = api_client_for(owner, organization).get("/api/v1/organizations/current/usage/")
        assert response.status_code == 200
        body = response.json()
        assert body["members"] == 2  # owner + the agent just added
        assert body["businesses"] == 1
        assert body["whatsapp_business_accounts"] == 1
        assert body["phone_numbers"] == 1
        assert body["contacts"] == 1
        assert body["messages_this_period"] == 0
        assert "period_start" in body

    def test_agent_cannot_view_usage(self, organization: Any) -> None:
        agent = factories.make_member(organization, "agent")
        response = api_client_for(agent, organization).get("/api/v1/organizations/current/usage/")
        assert response.status_code == 403

    def test_usage_is_scoped_to_the_calling_organization(
        self, owner: Any, organization: Any
    ) -> None:
        other_org = factories.make_organization(owner=owner)
        factories.make_business(other_org, "Other Biz")
        response = api_client_for(owner, organization).get("/api/v1/organizations/current/usage/")
        assert response.json()["businesses"] == 0
