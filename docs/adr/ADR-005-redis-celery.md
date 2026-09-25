# ADR-005: Modular monolith with Redis and Celery for async work

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

The platform must be async-first (webhooks, sends, campaigns, sync, analytics) without premature microservices.

## Decision

One Django codebase with strict module boundaries (service interfaces, no cross-module model writes). Celery with Redis as broker, split into queues by failure domain: `webhooks, outbound, campaigns, sync, analytics, default`. Tasks are idempotent (row-lock claims), `acks_late`, prefetch 1. Provider-calling tasks retry only errors classified as retryable.

## Consequences

- (+) One deploy, one transaction model, simple local development.
- (+) Queue isolation stops a bulk campaign from delaying agent replies or webhook processing.
- (-) Redis is a single point for the broker; AOF persistence plus the webhook outbox limit the impact. Modules can be extracted into services later along existing boundaries.
