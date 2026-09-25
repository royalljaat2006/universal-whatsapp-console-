"""POST/GET /webhooks/v1/meta/whatsapp/

Deliberately a plain Django view (not DRF): the signature must be computed
over the exact raw body bytes, and the endpoint must stay cheap —
verify, persist, respond. No domain processing happens here.
"""

import json
import logging
import time

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.webhooks.ingest import persist_event
from core.observability import capture_exception
from providers.whatsapp.errors import WebhookVerificationError
from providers.whatsapp.registry import get_webhook_adapter

logger = logging.getLogger(__name__)

PROVIDER = "meta"


def _handshake(request: HttpRequest) -> HttpResponse:
    challenge = get_webhook_adapter(PROVIDER).verify_subscription(request.GET)
    if challenge is None:
        logger.warning("webhook subscription handshake rejected")
        return HttpResponse(status=403)
    # Meta requires the challenge echoed as plain text.
    return HttpResponse(challenge, content_type="text/plain")


def _receive(request: HttpRequest) -> HttpResponse:
    started = time.perf_counter()
    raw_body = request.body
    if len(raw_body) > settings.WEBHOOK_MAX_BODY_BYTES:
        return HttpResponse(status=413)

    adapter = get_webhook_adapter(PROVIDER)
    try:
        adapter.verify_signature(raw_body, request.headers)
    except WebhookVerificationError as exc:
        # Unauthenticated bodies are never stored (prevents storage abuse and
        # injection of forged customer messages).
        logger.warning("webhook signature rejected", extra={"reason": str(exc)})
        return HttpResponse(status=401)

    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        logger.warning("webhook body is not valid JSON")
        return HttpResponse(status=400)
    if not isinstance(payload, dict):
        return HttpResponse(status=400)

    try:
        result = persist_event(
            provider=PROVIDER, adapter=adapter, raw_body=raw_body, payload=payload
        )
    except DatabaseError as exc:
        # Not acknowledged -> Meta retries (for up to 36h). Never 200 an event
        # we could not persist.
        capture_exception(exc, source="webhook_ingest", provider=PROVIDER)
        return HttpResponse(status=503)
    logger.info(
        "webhook accepted",
        extra={
            "provider": PROVIDER,
            "event_id": str(result.event.pk),
            "duplicate": result.duplicate,
            "ingest_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return JsonResponse({"status": "accepted"})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def meta_whatsapp_webhook(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return _handshake(request)
    return _receive(request)
