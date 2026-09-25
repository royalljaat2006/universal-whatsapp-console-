# ADR-004: PostgreSQL as the system of record

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

We need relational integrity (tenancy, uniqueness for idempotency), JSONB for provider payloads, partial and expression indexes, and a path to partitioning.

## Decision

PostgreSQL 17. Idempotency is enforced by database constraints (partial unique indexes, NULLS NOT DISTINCT), not by application checks. Tests run against real PostgreSQL, not SQLite.

## Consequences

- (+) Guarantees hold under concurrency and replays.
- (-) Tests need a running Postgres (docker compose provides it).
