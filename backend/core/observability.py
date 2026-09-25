"""Error-tracking hook.

``capture_exception`` is the single call site for unexpected errors (API
500s, webhook ingestion failures, Celery task failures). It always emits a
structured ``error.captured`` log line with a stable fingerprint and the
current correlation context. If ``ERROR_TRACKING_HOOK`` names a callable
(``"package.module:function"``), it is invoked too — that is where a Sentry
(or similar) adapter plugs in without touching call sites. No external
service is added in this phase (see infrastructure/monitoring/README.md).
"""

import hashlib
import importlib
import logging
import traceback
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from django.conf import settings

from core.context import snapshot

logger = logging.getLogger("errors")

Hook = Callable[[BaseException, dict[str, Any]], None]


def fingerprint(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)[-3:]
    key = type(exc).__name__ + "|" + "|".join(f"{f.filename}:{f.name}" for f in frames)
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]


@lru_cache(maxsize=1)
def _hook() -> Hook | None:
    path = getattr(settings, "ERROR_TRACKING_HOOK", "")
    if not path:
        return None
    module_name, _, attr = path.partition(":")
    hook: Hook = getattr(importlib.import_module(module_name), attr)
    return hook


def capture_exception(exc: BaseException, **context: Any) -> str:
    ctx = snapshot()
    data = {
        "fingerprint": fingerprint(exc),
        "error_type": type(exc).__name__,
        "request_id": ctx.request_id,
        **context,
    }
    logger.error("error.captured", exc_info=exc, extra=data)
    hook = _hook()
    if hook is not None:
        try:
            hook(exc, data)
        except Exception:  # a broken tracker must never break the caller
            logger.exception("error tracking hook failed")
    return str(data["fingerprint"])
