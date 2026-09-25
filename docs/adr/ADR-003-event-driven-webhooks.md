# ADR-003: Event-driven webhook ingestion (persist, acknowledge, process async)

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

Meta retries unacknowledged webhooks for up to 36h, provides no event ids, does not order deliveries, and can batch many tenants in one POST. Synchronous processing risks timeouts, duplicates and lost events.

## Decision

The HTTP handler only verifies the signature, stores the raw payload (JSONB, deduplicated by body SHA-256) and returns 200 after commit. A Celery `webhooks` queue processes events. Domain unique constraints make item processing idempotent. A beat sweeper re-enqueues pending, failed and stuck events (transactional outbox). Raw events are replayable by command or admin action.

## Consequences

- (+) No acknowledged event can be lost to a broker outage; state can be rebuilt by replay.
- (+) Webhook latency is independent of domain complexity.
- (-) Storage grows with traffic; the retention policy (90 days after processing) applies once enabled.
