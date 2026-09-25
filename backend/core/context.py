"""Request/job-scoped context carried through logs, tasks and audit records.

Context variables work across threads (sync views) and asyncio tasks
(Channels consumers) without leaking between concurrent requests.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
organization_id_var: ContextVar[str | None] = ContextVar("organization_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


@dataclass(frozen=True)
class ContextSnapshot:
    request_id: str | None
    organization_id: str | None
    user_id: str | None


def snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        request_id=request_id_var.get(),
        organization_id=organization_id_var.get(),
        user_id=user_id_var.get(),
    )


@contextmanager
def bind(
    *,
    request_id: str | None = None,
    organization_id: object | None = None,
    user_id: object | None = None,
) -> Iterator[None]:
    """Temporarily bind context values (only those passed are changed)."""
    tokens = []
    if request_id is not None:
        tokens.append((request_id_var, request_id_var.set(request_id)))
    if organization_id is not None:
        tokens.append((organization_id_var, organization_id_var.set(str(organization_id))))
    if user_id is not None:
        tokens.append((user_id_var, user_id_var.set(str(user_id))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
