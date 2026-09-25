# Webhook architecture

```
Meta ──POST──► nginx (rate-limit per IP) ──► webhooks.views.meta_whatsapp_webhook
                 1. body ≤ WEBHOOK_MAX_BODY_BYTES
                 2. verify X-Hub-Signature-256 over raw bytes   ──fail──► 401 (nothing stored)
                 3. parse JSON                                  ──fail──► 400
                 4. INSERT WebhookEvent(payload JSONB, body_sha256, routing keys, request_id)
                    ├─ unique(provider, body_sha256) hit → duplicate, return 200
                    └─ on_commit → process_webhook_event.delay(id)   (broker down? row stays PENDING)
                 5. 200 {"status":"accepted"}          ← no domain work on the request path

Celery "webhooks" queue ──► processor.process_event(id)
                 claim: SELECT … FOR UPDATE SKIP LOCKED WHERE status IN (pending, failed)
                 → status=PROCESSING, attempts+1, WebhookProcessingAttempt row
                 → MetaWebhookAdapter.parse(payload) → [InboundMessageEvent | StatusUpdateEvent |
                                                        TemplateStatusEvent | AccountEvent]
                 → handlers.handle(item) per item → ItemOutcome
                        APPLIED | IGNORED | UNROUTABLE | RETRY
                 → PROCESSED / PARTIAL / FAILED(+next_attempt_at) / DEAD
                 → realtime events published after each domain commit

beat (every 60s) ──► requeue_stale_webhook_events
                 → PENDING older than 30s, FAILED with next_attempt_at ≤ now,
                   PROCESSING stuck > 15 min (worker died) → re-enqueue
```

## Why raw storage first

- **Durability.** We acknowledge only after the row commits. Redis loss can
  delay processing but never drop an acknowledged event (transactional outbox).
- **Replayability.** Domain state is *derived* from raw events. Fix a processor
  bug, then `manage.py replay_webhook_events --status dead` (or an admin
  action) rebuilds correct state. Every domain write is idempotent, so replaying
  an already-processed event is safe (tested).
- **Debuggability.** Every processing run is a `WebhookProcessingAttempt` row
  with item counts, error and duration. Events carry the ingest `request_id`,
  which is re-bound in the worker so logs correlate end to end.

## Idempotency layers

1. Envelope: `unique(provider, body_sha256)`. Meta sends no event id.
2. Item: domain unique keys. The inbound message key is
   (number, direction, wamid). The status key is (message, status, timestamp).
   The contact identity and conversation thread keys are also unique.
3. Processing: row claim with `SKIP LOCKED`, so two workers never process the
   same event at once.

## Multi-tenant routing

One delivery can contain several WABAs. The raw event is therefore not
tenant-owned. Each item is routed by `metadata.phone_number_id` (messages and
statuses) or `entry.id` (WABA-level fields) to the owning organization, and the
event records `organization_ids[]`. Unknown numbers or WABAs → item
`UNROUTABLE`, event `PARTIAL` (no retry storm, still replayable after the
number is connected).

## Ordering and races

- Meta does not deliver in chronological order. The message state machine
  uses provider timestamps and never regresses (see messaging-architecture.md).
- A status can arrive before our send transaction has stored the wamid. The
  item returns `RETRY`, and the event is re-run after 20s for up to
  `WEBHOOK_UNMATCHED_STATUS_RETRY_SECONDS` (default 10 min). After that it
  counts as unroutable (typically a message sent outside this platform).
- Template status events older than the last applied one are ignored.

## Failure handling

| Failure | Result |
|---|---|
| Handler exception | FAILED, backoff 15s → 1m → 5m → 15m → 30m → 60m; DEAD after `WEBHOOK_MAX_PROCESSING_ATTEMPTS` (8) |
| Malformed single change | Skipped and logged; the rest of the batch is processed |
| Worker crash mid-processing | Sweeper resets PROCESSING > 15 min to FAILED |
| Broker down at ingest | Row stays PENDING and the sweeper enqueues it |

## Security

- HMAC-SHA256 with the App Secret, constant-time compare, over the raw body.
  Unsigned bodies are never persisted.
- Handshake token compared in constant time. Returns 403 when unconfigured.
- nginx `limit_req` on `/webhooks/` and a body-size limit in Django.
