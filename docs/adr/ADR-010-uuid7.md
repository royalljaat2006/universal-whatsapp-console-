# ADR-010: UUIDv7 primary keys

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

Identifiers appear in URLs and payloads; high-volume tables need insert locality.

## Decision

All domain tables use time-ordered UUIDv7 generated in `core/ids.py` (monotonic per process).

## Consequences

- (+) Non-enumerable, generated before insert, append-friendly B-trees, partition-friendly.
- (-) 16-byte keys instead of 8, acceptable.
