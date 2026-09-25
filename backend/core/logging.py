"""Structured logging.

Every record is enriched with request_id / organization_id / user_id from
``core.context`` and scrubbed of anything that looks like a credential.
Emit domain fields through ``extra={...}``; never interpolate secrets.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from core.context import organization_id_var, request_id_var, user_id_var

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
    | {"message", "asctime", "request_id", "organization_id", "user_id"}
)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|signature|cookie|credential)",
    re.IGNORECASE,
)
# Bearer tokens and Meta long-lived tokens (EAA...) inside free text.
_SENSITIVE_VALUE_PATTERN = re.compile(r"(Bearer\s+[A-Za-z0-9._\-]+|EAA[A-Za-z0-9]{20,})")

REDACTED = "[REDACTED]"


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys/values. Safe for arbitrary JSON."""
    if _depth > 8:
        return value
    if isinstance(value, dict):
        return {
            k: (REDACTED if SENSITIVE_KEY_PATTERN.search(str(k)) else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE_PATTERN.sub(REDACTED, value)
    return value


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.organization_id = organization_id_var.get()
        record.user_id = user_id_var.get()
        return True


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", None),
            "organization_id": getattr(record, "organization_id", None),
            "user_id": getattr(record, "user_id", None),
        }
        payload.update(redact(_extras(record)))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line for local development."""

    def format(self, record: logging.LogRecord) -> str:
        extras = redact(_extras(record))
        ctx = " ".join(
            f"{k}={v}"
            for k, v in (
                ("req", getattr(record, "request_id", None)),
                ("org", getattr(record, "organization_id", None)),
            )
            if v
        )
        kv = " ".join(f"{k}={v}" for k, v in extras.items())
        line = f"{record.levelname:<7} {record.name}: {redact(record.getMessage())}"
        line = " ".join(part for part in (line, ctx, kv) if part)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line
