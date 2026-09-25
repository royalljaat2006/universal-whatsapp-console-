"""Propagate correlation context from the task publisher to the worker.

The publisher's request_id / organization_id / user_id travel as message
headers and are re-bound in the worker, so a webhook request and the jobs
it triggers share one request_id in the logs.
"""

import uuid
from typing import Any

from celery.signals import before_task_publish, task_failure, task_postrun, task_prerun

from core.context import organization_id_var, request_id_var, user_id_var

_HEADER_KEYS = {
    "x_request_id": request_id_var,
    "x_organization_id": organization_id_var,
    "x_user_id": user_id_var,
}
_TOKENS: dict[str, list[Any]] = {}


@before_task_publish.connect
def _inject_context(headers: dict[str, Any] | None = None, **_: Any) -> None:
    if headers is None:
        return
    for key, var in _HEADER_KEYS.items():
        value = var.get()
        if value is not None:
            headers.setdefault(key, value)


@task_prerun.connect
def _bind_context(task_id: str | None = None, task: Any = None, **_: Any) -> None:
    request = getattr(task, "request", None)
    tokens = []
    for key, var in _HEADER_KEYS.items():
        value = getattr(request, key, None) if request is not None else None
        if key == "x_request_id" and not value:
            value = f"task-{task_id or uuid.uuid4().hex}"
        tokens.append((var, var.set(value)))
    if task_id:
        _TOKENS[task_id] = tokens


@task_postrun.connect
def _unbind_context(task_id: str | None = None, **_: Any) -> None:
    for var, token in reversed(_TOKENS.pop(task_id or "", [])):
        try:
            var.reset(token)
        except ValueError:  # token created in another context (eager mode)
            var.set(None)


@task_failure.connect
def _capture_failure(
    sender: Any = None, task_id: str | None = None, exception: BaseException | None = None, **_: Any
) -> None:
    if exception is None:
        return
    from core.observability import capture_exception

    capture_exception(
        exception, source="celery", task=getattr(sender, "name", str(sender)), task_id=task_id
    )
