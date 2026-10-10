"""Backup history API and the Prometheus gauges derived from core.BackupRecord
(rows are written by infrastructure/docker/backup/entrypoint.sh)."""

import math
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from core.metrics import REGISTRY, refresh_backup_gauges
from core.models import BackupRecord
from tests import factories
from tests.conftest import api_client_for

pytestmark = pytest.mark.django_db

URL = "/api/v1/platform/backups/"


def _staff_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(user=factories.make_user(is_staff=True))
    return client


def _record(**overrides: Any) -> BackupRecord:
    now = timezone.now()
    values: dict[str, Any] = {
        "started_at": now,
        "finished_at": now,
        "status": BackupRecord.Status.SUCCEEDED,
        "file_path": "/backups/db-20260101T000000Z.dump.enc",
        "file_size_bytes": 1234,
        "sha256": "a" * 64,
        "encrypted": True,
        "disk_free_bytes": 500,
        "disk_total_bytes": 1000,
    }
    values.update(overrides)
    return BackupRecord.objects.create(**values)


def _gauge(name: str) -> float | None:
    return REGISTRY.get_sample_value(name)


class TestBackupHistoryApi:
    def test_regular_organization_owner_is_forbidden(self, owner: Any, organization: Any) -> None:
        _record()
        assert api_client_for(owner, organization).get(URL).status_code == 403

    def test_unauthenticated_is_denied(self) -> None:
        assert APIClient().get(URL).status_code in (401, 403)

    def test_staff_sees_newest_first_without_secrets(self) -> None:
        old = _record(started_at=timezone.now() - timedelta(days=1), file_path="/backups/old")
        new = _record(
            status=BackupRecord.Status.FAILED, error_message="pg_dump failed: boom", file_path=""
        )
        body = _staff_client().get(URL).json()
        rows = body["results"] if isinstance(body, dict) else body
        assert [r["id"] for r in rows] == [new.pk, old.pk]
        assert rows[0]["status"] == "failed"
        assert rows[0]["error_message"] == "pg_dump failed: boom"
        assert set(rows[0]) == {
            "id", "started_at", "finished_at", "status", "file_path", "file_size_bytes",
            "sha256", "encrypted", "error_message", "disk_free_bytes", "disk_total_bytes",
        }  # fmt: skip

    def test_history_is_read_only(self) -> None:
        assert _staff_client().post(URL, {}, format="json").status_code == 405


class TestBackupGauges:
    def test_no_records_reports_missing_not_fresh(self) -> None:
        refresh_backup_gauges()
        assert _gauge("backup_last_success_age_seconds") == math.inf
        assert math.isnan(_gauge("backup_last_status"))  # type: ignore[arg-type]
        assert math.isnan(_gauge("backup_disk_free_ratio"))  # type: ignore[arg-type]

    def test_only_failures_still_reports_missing(self) -> None:
        _record(status=BackupRecord.Status.FAILED, error_message="x", disk_free_bytes=None)
        refresh_backup_gauges()
        assert _gauge("backup_last_status") == 0
        assert _gauge("backup_last_success_age_seconds") == math.inf

    def test_success_reports_age_size_and_disk_ratio(self) -> None:
        _record(started_at=timezone.now() - timedelta(hours=3))
        refresh_backup_gauges()
        assert _gauge("backup_last_status") == 1
        age = _gauge("backup_last_success_age_seconds")
        assert age is not None and 3 * 3600 <= age < 3 * 3600 + 60
        assert _gauge("backup_last_size_bytes") == 1234
        assert _gauge("backup_disk_free_ratio") == 0.5

    def test_failure_after_success_flips_status_but_keeps_last_success_age(self) -> None:
        _record(started_at=timezone.now() - timedelta(hours=5))
        _record(
            started_at=timezone.now() - timedelta(hours=1),
            status=BackupRecord.Status.FAILED,
            error_message="disk full",
        )
        refresh_backup_gauges()
        assert _gauge("backup_last_status") == 0
        age = _gauge("backup_last_success_age_seconds")
        assert age is not None and 5 * 3600 <= age < 5 * 3600 + 60
