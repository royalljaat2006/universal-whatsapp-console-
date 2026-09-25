"""Cycle 3 CSV exports: asynchronous, business-scoped, chunked, injection-safe."""

import csv
import io
from typing import Any

import pytest

from apps.campaigns.models import Campaign, CampaignRecipient
from apps.reports.models import ExportJob
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db(transaction=True)


def _campaign(business: Any, phone: Any, template: Any, recipients: int) -> Campaign:
    campaign = Campaign.objects.create(
        organization=business.organization,
        business=business,
        name="Export me",
        phone_number=phone,
        template=template,
        status=Campaign.Status.COMPLETED,
    )
    contacts = [
        factories.make_contact(
            business, f"9197{i:08d}", name='=HYPERLINK("x")' if i == 0 else f"C{i}"
        )
        for i in range(recipients)
    ]
    CampaignRecipient.objects.bulk_create(
        [
            CampaignRecipient(
                organization=business.organization,
                campaign=campaign,
                contact=c,
                phone_number=c.phone_number,
                status=CampaignRecipient.Status.SKIPPED,
                skip_reason="VARIABLE_MISSING",
                attempts=1,
            )
            for c in contacts
        ]
    )
    return campaign


def _download(client: Any, job_id: str) -> list[list[str]]:
    response = client.get(f"/api/v1/exports/{job_id}/download/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    body = b"".join(response.streaming_content).decode()
    return list(csv.reader(io.StringIO(body)))


@pytest.fixture
def world(owner: Any, organization: Any) -> dict[str, Any]:
    kiosk = factories.make_business(organization, "Kiosk")
    eps = factories.make_business(organization, "EPS")
    kiosk_waba = factories.make_waba(organization, business=kiosk)
    eps_waba = factories.make_waba(organization, business=eps)
    return {
        "kiosk": kiosk,
        "eps": eps,
        "kiosk_phone": factories.make_phone_number(kiosk_waba),
        "eps_phone": factories.make_phone_number(eps_waba),
        "kiosk_template": factories.make_campaign_template(kiosk_waba),
        "eps_template": factories.make_campaign_template(eps_waba),
        "api": api_client_for(owner, organization, kiosk),
        "eps_api": api_client_for(owner, organization, eps),
    }


def test_campaign_recipient_export_is_async_correct_and_injection_safe(
    world: dict[str, Any],
) -> None:
    campaign = _campaign(world["kiosk"], world["kiosk_phone"], world["kiosk_template"], 2500)
    response = world["api"].post(
        "/api/v1/exports/",
        {"kind": "campaign_recipients", "params": {"campaign_id": str(campaign.pk)}},
        format="json",
    )
    assert response.status_code == 202
    job = ExportJob.objects.get(pk=response.json()["id"])
    assert job.status == ExportJob.Status.SUCCEEDED  # eager worker in tests
    assert job.row_count == 2500
    rows = _download(world["api"], str(job.pk))
    assert rows[0][:5] == ["recipient_id", "contact_id", "contact_name", "phone_number", "status"]
    assert len(rows) == 2501
    names = {r[2] for r in rows[1:]}
    assert '\'=HYPERLINK("x")' in names  # formula neutralised
    assert all(r[4] == "skipped" for r in rows[1:])


def test_exports_are_business_isolated(world: dict[str, Any], organization: Any) -> None:
    eps_campaign = _campaign(world["eps"], world["eps_phone"], world["eps_template"], 1)
    foreign = world["api"].post(
        "/api/v1/exports/",
        {"kind": "campaign_summary", "params": {"campaign_id": str(eps_campaign.pk)}},
        format="json",
    )
    assert foreign.status_code == 404
    job = (
        world["eps_api"]
        .post(
            "/api/v1/exports/",
            {"kind": "campaign_summary", "params": {"campaign_id": str(eps_campaign.pk)}},
            format="json",
        )
        .json()
    )
    kiosk_only = factories.make_member(organization, "manager", businesses=[world["kiosk"]])
    client = api_client_for(kiosk_only, organization)
    assert client.get(f"/api/v1/exports/{job['id']}/download/").status_code == 404
    assert client.get(f"/api/v1/exports/{job['id']}/").status_code == 404
    summary = _download(world["eps_api"], job["id"])
    assert ["name", "Export me"] in summary


def test_export_requires_permissions(world: dict[str, Any], organization: Any) -> None:
    agent = factories.make_member(organization, "agent", businesses=[world["kiosk"]])
    client = api_client_for(agent, organization, world["kiosk"])
    response = client.post(
        "/api/v1/exports/",
        {"kind": "messages", "params": {"date_from": "2026-09-01", "date_to": "2026-09-30"}},
        format="json",
    )
    assert response.status_code == 403
    bad_range = world["api"].post(
        "/api/v1/exports/",
        {"kind": "messages", "params": {"date_from": "2026-09-30", "date_to": "2026-09-01"}},
        format="json",
    )
    assert bad_range.status_code == 400


def test_messages_and_analytics_exports(world: dict[str, Any]) -> None:
    from apps.audit.models import AuditLog

    for kind in ("messages", "costs", "analytics_daily"):
        response = world["api"].post(
            "/api/v1/exports/",
            {"kind": kind, "params": {"date_from": "2026-09-01", "date_to": "2026-09-30"}},
            format="json",
        )
        assert response.status_code == 202, response.json()
        job = ExportJob.objects.get(pk=response.json()["id"])
        assert job.status == ExportJob.Status.SUCCEEDED and job.row_count == 0
        rows = _download(world["api"], str(job.pk))
        assert len(rows) == 1  # header only: no data, nothing invented
    assert AuditLog.objects.filter(action="report.export_requested").count() == 3
    assert AuditLog.objects.filter(action="report.export_downloaded").count() == 3
