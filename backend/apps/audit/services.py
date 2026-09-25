"""Public interface of the audit module. Other modules call ``record`` only."""

from typing import Any

from django.http import HttpRequest

from apps.audit.models import AuditLog
from core.context import request_id_var
from core.logging import redact


def _client_ip(request: HttpRequest) -> str | None:
    # nginx sets X-Real-IP from the socket peer; X-Forwarded-For can be spoofed
    # by clients and is therefore not trusted here.
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or None


def record(
    action: str,
    *,
    organization: Any = None,
    actor: Any = None,
    resource: Any = None,
    resource_type: str = "",
    resource_id: str = "",
    metadata: dict[str, Any] | None = None,
    request: HttpRequest | None = None,
) -> AuditLog:
    if resource is not None:
        resource_type = resource_type or resource._meta.label_lower
        resource_id = resource_id or str(resource.pk)
    is_user = actor is not None and getattr(actor, "is_authenticated", False)
    return AuditLog.objects.create(
        organization=organization,
        actor=actor if is_user else None,
        actor_type=AuditLog.ActorType.USER if is_user else AuditLog.ActorType.SYSTEM,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=redact(metadata or {}),
        ip_address=_client_ip(request) if request else None,
        user_agent=(request.headers.get("User-Agent", "")[:512] if request else ""),
        request_id=request_id_var.get() or "",
    )
