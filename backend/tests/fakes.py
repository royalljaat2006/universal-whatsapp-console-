"""Fake Meta Graph API for tests (an httpx transport — no sockets opened)."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

Responder = Callable[[httpx.Request], httpx.Response]


class UnexpectedGraphCall(AssertionError):
    pass


@dataclass
class FakeGraph:
    """Route table keyed by (METHOD, path-without-version). Unrouted calls fail
    the test loudly, so no code path can silently hit the real network."""

    routes: dict[tuple[str, str], list[Responder]] = field(default_factory=dict)
    calls: list[httpx.Request] = field(default_factory=list)

    def on(self, method: str, path: str, *responders: Responder | dict[str, Any]) -> None:
        queue = self.routes.setdefault((method.upper(), path), [])
        for responder in responders:
            if isinstance(responder, dict):
                queue.append(json_response(200, responder))
            else:
                queue.append(responder)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host != "graph.facebook.com":
            # Media downloads come from Meta's media host: route by host+path.
            path = f"{request.url.host}{request.url.path}"
        else:
            parts = request.url.path.split("/", 2)  # ['', 'v26.0', 'rest']
            path = "/" + (parts[2] if len(parts) > 2 else "")
        queue = self.routes.get((request.method, path))
        if not queue:
            raise UnexpectedGraphCall(f"unrouted Graph call {request.method} {request.url}")
        responder = queue.pop(0) if len(queue) > 1 else queue[0]
        return responder(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def json_bodies(self, method: str, path: str) -> list[dict[str, Any]]:
        return [
            json.loads(c.content or b"{}")
            for c in self.calls
            if c.method == method and c.url.path.endswith(path)
        ]


def json_response(status: int, body: dict[str, Any]) -> Responder:
    return lambda request: httpx.Response(status, json=body)


def graph_error(status: int, code: int, message: str = "error") -> Responder:
    return json_response(
        status,
        {"error": {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "T"}},
    )


def bytes_response(
    status: int, data: bytes, content_type: str = "application/octet-stream"
) -> Responder:
    return lambda request: httpx.Response(
        status, content=data, headers={"Content-Type": content_type}
    )


def raise_exc(exc: Exception) -> Responder:
    def responder(request: httpx.Request) -> httpx.Response:
        raise exc

    return responder


def accept_send(request: httpx.Request) -> httpx.Response:
    """Cloud API send acceptance with a fresh wamid per call (like Meta)."""
    import uuid

    body = json.loads(request.content or b"{}")
    to = body.get("to") or body.get("recipient") or ""
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": to, "wa_id": to}],
            "messages": [{"id": f"wamid.{uuid.uuid4().hex}", "message_status": "accepted"}],
        },
    )
