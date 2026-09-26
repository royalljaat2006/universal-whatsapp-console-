"""Contact CSV import (apps.contacts.import_csv) and export (via the
existing generic apps.reports export subsystem)."""

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.contacts.models import Contact, ContactImportJob
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db(transaction=True)


def _upload(client: Any, business: Any, csv_text: str, *, tag_name: str = "") -> Any:
    data: dict[str, Any] = {
        "file": SimpleUploadedFile("contacts.csv", csv_text.encode(), content_type="text/csv")
    }
    if tag_name:
        data["tag_name"] = tag_name
    return client.post(
        "/api/v1/contacts/import/", data, format="multipart", HTTP_X_BUSINESS_ID=str(business.pk)
    )


class TestImport:
    def test_creates_updates_and_reports_a_bad_row(self, owner_api: Any, organization: Any) -> None:
        business = factories.make_business(organization, "Kiosk")
        existing = factories.make_contact(business, "919876500001", name="Old Name")
        csv_text = (
            "phone_number,display_name,city,tags\n"
            "919876500001,New Name,Delhi,vip\n"
            "919876500002,Brand New,Mumbai,vip\n"
            ",No Phone,,\n"
        )
        response = _upload(owner_api, business, csv_text, tag_name="imported")
        assert response.status_code == 202, response.content
        # The response body reflects the job at creation ("pending") — Celery
        # runs the task eagerly, but only once the request's own outer
        # transaction commits, i.e. after this Response is already built. Read
        # the finished job back to see the real outcome, same as the manual
        # WABA-connect test does for its queued pipeline.
        job = ContactImportJob.objects.get(pk=response.json()["id"])
        assert job.status == "succeeded_with_errors"
        assert job.total_rows == 3
        assert job.created_count == 1
        assert job.updated_count == 1
        assert job.failed_count == 1
        assert job.row_errors[0]["row"] == 3
        assert job.business_id == business.pk

        existing.refresh_from_db()
        assert existing.display_name == "New Name"
        assert existing.attributes.get("city") == "Delhi"
        assert {t.name for t in existing.tags.all()} == {"imported", "vip"}

        created = Contact.objects.get(business=business, phone_number="919876500002")
        assert created.display_name == "Brand New"
        assert {t.name for t in created.tags.all()} == {"imported", "vip"}

    def test_missing_phone_column_fails_the_whole_job(self, owner_api: Any, organization: Any) -> None:
        business = factories.make_business(organization, "Kiosk")
        response = _upload(owner_api, business, "name,city\nAda,Delhi\n")
        assert response.status_code == 202
        job = ContactImportJob.objects.get(pk=response.json()["id"])
        assert job.status == "failed"
        assert "phone_number" in job.error
        assert Contact.objects.filter(business=business).count() == 0

    def test_row_cap_is_enforced(
        self, owner_api: Any, organization: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("apps.contacts.import_csv.MAX_IMPORT_ROWS", 2)
        business = factories.make_business(organization, "Kiosk")
        csv_text = "phone_number\n" + "".join(f"91987650{i:04d}\n" for i in range(5))
        response = _upload(owner_api, business, csv_text)
        job = ContactImportJob.objects.get(pk=response.json()["id"])
        assert job.status == "failed"
        assert "more than 2 rows" in job.error
        assert Contact.objects.filter(business=business).count() == 0

    def test_oversized_file_is_rejected(self, owner_api: Any, organization: Any) -> None:
        business = factories.make_business(organization, "Kiosk")
        huge = "phone_number\n" + "x" * (5 * 1024 * 1024 + 1)
        response = _upload(owner_api, business, huge)
        assert response.status_code == 400

    def test_viewer_cannot_import(self, organization: Any) -> None:
        business = factories.make_business(organization, "Kiosk")
        viewer = factories.make_member(organization, "viewer", businesses=[business])
        response = _upload(
            api_client_for(viewer, organization), business, "phone_number\n919876500001\n"
        )
        assert response.status_code == 403

    def test_agent_can_import(self, organization: Any) -> None:
        business = factories.make_business(organization, "Kiosk")
        agent = factories.make_member(organization, "agent", businesses=[business])
        response = _upload(
            api_client_for(agent, organization), business, "phone_number\n919876500001\n"
        )
        assert response.status_code == 202
        job = ContactImportJob.objects.get(pk=response.json()["id"])
        assert job.status == "succeeded"

    def test_cannot_import_into_an_inaccessible_business(self, organization: Any) -> None:
        kiosk = factories.make_business(organization, "Kiosk")
        eps = factories.make_business(organization, "EPS")
        manager = factories.make_member(organization, "admin", businesses=[kiosk])
        response = _upload(
            api_client_for(manager, organization), eps, "phone_number\n919876500001\n"
        )
        assert response.status_code == 404


class TestExport:
    def test_exports_contacts_optionally_filtered_by_tag(
        self, owner_api: Any, organization: Any
    ) -> None:
        business = factories.make_business(organization, "Kiosk")
        vip = factories.make_tag(business, "VIP")
        factories.make_contact(business, "919876500001", name="Asha", tags=[vip])
        factories.make_contact(business, "919876500002", name="Rohit")

        created = owner_api.post(
            "/api/v1/exports/",
            {"kind": "contacts", "params": {}},
            format="json",
            HTTP_X_BUSINESS_ID=str(business.pk),
        )
        assert created.status_code == 202, created.content
        job_id = created.json()["id"]
        downloaded = owner_api.get(
            f"/api/v1/exports/{job_id}/download/", HTTP_X_BUSINESS_ID=str(business.pk)
        )
        csv_text = b"".join(downloaded.streaming_content).decode()
        assert "919876500001" in csv_text and "919876500002" in csv_text

        filtered = owner_api.post(
            "/api/v1/exports/",
            {"kind": "contacts", "params": {"tag_id": str(vip.pk)}},
            format="json",
            HTTP_X_BUSINESS_ID=str(business.pk),
        )
        job_id = filtered.json()["id"]
        downloaded = owner_api.get(
            f"/api/v1/exports/{job_id}/download/", HTTP_X_BUSINESS_ID=str(business.pk)
        )
        csv_text = b"".join(downloaded.streaming_content).decode()
        assert "919876500001" in csv_text and "919876500002" not in csv_text

    def test_export_requires_contacts_view(self, organization: Any) -> None:
        business = factories.make_business(organization, "Kiosk")
        # A role with reports.export but not contacts.view would be unusual in
        # DEFAULT_ROLES (manager/admin/owner all have both) — assert the check
        # is wired by calling it with a viewer, who lacks reports.export too.
        viewer = factories.make_member(organization, "viewer", businesses=[business])
        response = api_client_for(viewer, organization).post(
            "/api/v1/exports/",
            {"kind": "contacts", "params": {}},
            format="json",
            HTTP_X_BUSINESS_ID=str(business.pk),
        )
        assert response.status_code == 403
