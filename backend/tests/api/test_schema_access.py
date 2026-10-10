"""The OpenAPI schema lists every endpoint and field; outside DEBUG it is for
signed-in users only."""

from typing import Any

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def test_schema_is_not_public_outside_debug(settings: Any) -> None:
    assert settings.DEBUG is False
    assert APIClient().get("/api/v1/schema/").status_code in (401, 403)


def test_signed_in_users_can_read_the_schema(owner: Any) -> None:
    client = APIClient()
    client.force_authenticate(owner)
    assert client.get("/api/v1/schema/").status_code == 200
