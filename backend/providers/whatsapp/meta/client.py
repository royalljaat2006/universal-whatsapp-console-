"""Thin Graph API HTTP client: timeouts, error mapping, latency logging,
safe pagination. Never logs tokens, request bodies or response bodies."""

import logging
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from providers.whatsapp.errors import ProviderError, Retryability
from providers.whatsapp.meta.config import MetaConfig
from providers.whatsapp.meta.errors import error_from_response

logger = logging.getLogger("providers.meta")

_MAX_PAGES = 200


class GraphClient:
    def __init__(self, config: MetaConfig, *, transport: httpx.BaseTransport | None = None):
        self._config = config
        self._http = httpx.Client(
            base_url=config.versioned_base_url,
            timeout=httpx.Timeout(config.timeout_seconds, connect=5.0),
            transport=transport,
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        self._allowed_host = urlsplit(config.graph_base_url).netloc

    def close(self) -> None:
        self._http.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        absolute_url: str | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # ``token=None`` only for the Embedded Signup code exchange (see
        # MetaWhatsAppProvider.exchange_code_for_token), which authenticates via
        # client_id/client_secret query params instead of a bearer token — there
        # is no token to send yet, that's the point of the call.
        idempotent = method.upper() in {"GET", "HEAD"}
        url = absolute_url or path
        started = time.perf_counter()
        status: int | None = None
        try:
            response = self._http.request(
                method,
                url,
                params=params,
                json=json,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
            status = response.status_code
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # The request never reached Meta: always safe to retry.
            raise ProviderError(
                "Could not connect to Meta Graph API",
                code="PROVIDER_UNREACHABLE",
                retryability=Retryability.SAFE,
            ) from exc
        except httpx.TransportError as exc:
            # Sent (or partially sent) but no reliable answer.
            raise ProviderError(
                f"Meta Graph API transport error: {type(exc).__name__}",
                code="PROVIDER_OUTCOME_UNKNOWN",
                retryability=Retryability.TRANSIENT if idempotent else Retryability.AMBIGUOUS,
            ) from exc
        finally:
            logger.info(
                "meta graph call",
                extra={
                    "provider": "meta",
                    "http_method": method.upper(),
                    "graph_path": urlsplit(str(url)).path,
                    "http_status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )

        body = self._json(response)
        if response.is_success:
            return body
        raise error_from_response(response.status_code, body, idempotent=idempotent)

    def iter_pages(
        self, path: str, *, token: str, params: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield items across cursor pages. Follows ``paging.next`` only when it
        points at the configured Graph host (guards against SSRF via a
        tampered or unexpected ``next`` URL)."""
        page = self.request("GET", path, token=token, params=params)
        for _ in range(_MAX_PAGES):
            yield from page.get("data") or []
            next_url = (page.get("paging") or {}).get("next")
            if not next_url:
                return
            parts = urlsplit(next_url)
            if parts.scheme != "https" or parts.netloc != self._allowed_host:
                logger.warning(
                    "refusing to follow foreign paging url", extra={"host": parts.netloc}
                )
                return
            page = self.request("GET", "", token=token, absolute_url=next_url)
        logger.warning("pagination limit reached", extra={"graph_path": path})

    def download(self, url: str, *, token: str, allowed_hosts: list[str], max_bytes: int) -> bytes:
        """Stream a media file from a provider URL. Only https URLs on an
        allow-listed host are fetched (SSRF guard); the size cap is enforced
        while streaming, not after."""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in allowed_hosts:
            raise ProviderError(
                "Media URL host is not allowed",
                code="MEDIA_URL_REJECTED",
                retryability=Retryability.PERMANENT,
                details={"host": parts.hostname},
            )
        started = time.perf_counter()
        status: int | None = None
        try:
            with self._http.stream(
                "GET", url, headers={"Authorization": f"Bearer {token}"}
            ) as response:
                status = response.status_code
                if response.status_code == 404:
                    # Documented: expired media URL -> request a new URL.
                    raise ProviderError(
                        "Media URL expired",
                        code="MEDIA_URL_EXPIRED",
                        retryability=Retryability.SAFE,
                        http_status=404,
                    )
                if not response.is_success:
                    raise ProviderError(
                        f"Media download failed with HTTP {response.status_code}",
                        code="MEDIA_DOWNLOAD_FAILED",
                        retryability=(
                            Retryability.TRANSIENT
                            if response.status_code >= 500 or response.status_code == 429
                            else Retryability.PERMANENT
                        ),
                        http_status=response.status_code,
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ProviderError(
                            "Media exceeds the allowed size",
                            code="MEDIA_TOO_LARGE",
                            retryability=Retryability.PERMANENT,
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise ProviderError(
                "Could not connect to the media host",
                code="PROVIDER_UNREACHABLE",
                retryability=Retryability.SAFE,
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                f"Media download transport error: {type(exc).__name__}",
                code="MEDIA_DOWNLOAD_FAILED",
                retryability=Retryability.TRANSIENT,
            ) from exc
        finally:
            logger.info(
                "meta media download",
                extra={
                    "provider": "meta",
                    "media_host": parts.hostname,
                    "http_status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {"data": data}
