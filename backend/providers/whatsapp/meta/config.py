"""Meta adapter configuration, read from Django settings in one place."""

import re
from dataclasses import dataclass

from django.conf import settings

_VERSION_PATTERN = re.compile(r"^v\d+\.\d+$")


@dataclass(frozen=True)
class MetaConfig:
    graph_base_url: str
    graph_version: str
    timeout_seconds: float
    app_secret: str
    webhook_verify_token: str

    def __post_init__(self) -> None:
        if not _VERSION_PATTERN.match(self.graph_version):
            raise ValueError(
                f"META_GRAPH_API_VERSION must look like 'v26.0', got {self.graph_version!r}"
            )
        if not self.graph_base_url.startswith("https://"):
            raise ValueError("META_GRAPH_API_BASE_URL must be https")

    @property
    def versioned_base_url(self) -> str:
        return f"{self.graph_base_url.rstrip('/')}/{self.graph_version}"

    @classmethod
    def from_settings(cls) -> "MetaConfig":
        return cls(
            graph_base_url=settings.META_GRAPH_API_BASE_URL,
            graph_version=settings.META_GRAPH_API_VERSION,
            timeout_seconds=settings.META_HTTP_TIMEOUT_SECONDS,
            app_secret=settings.META_APP_SECRET,
            webhook_verify_token=settings.META_WEBHOOK_VERIFY_TOKEN,
        )
