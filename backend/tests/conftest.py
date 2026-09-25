import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from providers.whatsapp import registry
from tests import factories
from tests.fakes import FakeGraph

FIXTURES = Path(__file__).parent / "fixtures" / "meta"


@pytest.fixture(autouse=True)
def media_storage(tmp_path: Path, settings: Any) -> Path:
    """Every test gets a private, empty media storage directory."""
    root = tmp_path / "media"
    settings.STORAGES = {
        **settings.STORAGES,
        "media": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(root), "base_url": None},
        },
    }
    return root


@pytest.fixture(autouse=True)
def fake_graph(monkeypatch: pytest.MonkeyPatch) -> FakeGraph:
    """Every test gets a fake Graph API; real network calls are impossible."""
    graph = FakeGraph()
    registry.reset(transport=graph.transport)
    monkeypatch.setenv(factories.TEST_TOKEN_ENV, factories.TEST_TOKEN)
    yield graph
    registry.reset()


def load_fixture(name: str, **replacements: str) -> dict[str, Any]:
    raw = (FIXTURES / name).read_text()
    for key, value in replacements.items():
        raw = raw.replace("{{" + key + "}}", value)
    return json.loads(raw)


def sign(body: bytes, secret: str | None = None) -> str:
    key = (secret or settings.META_APP_SECRET).encode()
    return "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()


@pytest.fixture
def post_webhook(client: Any) -> Any:
    def _post(payload: dict[str, Any], *, signature: str | None = None) -> Any:
        body = json.dumps(payload).encode()
        return client.post(
            "/webhooks/v1/meta/whatsapp/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature if signature is not None else sign(body),
        )

    return _post


@pytest.fixture
def owner() -> Any:
    return factories.make_user()


@pytest.fixture
def organization(owner: Any) -> Any:
    return factories.make_organization(owner=owner)


@pytest.fixture
def waba(organization: Any) -> Any:
    return factories.make_waba(organization)


@pytest.fixture
def phone(waba: Any) -> Any:
    return factories.make_phone_number(waba)


def api_client_for(
    user: Any, organization: Any | None = None, business: Any | None = None
) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    headers = {}
    if organization is not None:
        headers["HTTP_X_ORGANIZATION_ID"] = str(organization.pk)
    if business is not None:
        headers["HTTP_X_BUSINESS_ID"] = str(business.pk)
    client.credentials(**headers)
    return client


@pytest.fixture
def owner_api(owner: Any, organization: Any) -> APIClient:
    return api_client_for(owner, organization)
