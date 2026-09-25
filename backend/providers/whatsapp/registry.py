"""Provider lookup. Domain code obtains providers only through this module,
which is also the seam tests use to substitute a fake HTTP transport."""

from functools import lru_cache

import httpx

from providers.whatsapp.base import WebhookAdapter, WhatsAppProvider
from providers.whatsapp.meta.config import MetaConfig
from providers.whatsapp.meta.provider import MetaWhatsAppProvider
from providers.whatsapp.meta.webhooks import MetaWebhookAdapter

_transport_override: httpx.BaseTransport | None = None


class UnknownProviderError(LookupError):
    pass


@lru_cache(maxsize=8)
def get_provider(name: str) -> WhatsAppProvider:
    if name == "meta":
        from django.conf import settings

        return MetaWhatsAppProvider(
            MetaConfig.from_settings(),
            transport=_transport_override,
            media_hosts=list(settings.META_MEDIA_DOWNLOAD_HOSTS),
        )
    raise UnknownProviderError(name)


@lru_cache(maxsize=8)
def get_webhook_adapter(name: str) -> WebhookAdapter:
    if name == "meta":
        return MetaWebhookAdapter(MetaConfig.from_settings())
    raise UnknownProviderError(name)


def reset(transport: httpx.BaseTransport | None = None) -> None:
    """Clear cached providers (settings changed) and optionally install an
    HTTP transport — used by tests to guarantee no real network calls."""
    global _transport_override
    _transport_override = transport
    get_provider.cache_clear()
    get_webhook_adapter.cache_clear()
