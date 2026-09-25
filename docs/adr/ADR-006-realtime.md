# ADR-006: Realtime via Django Channels and a Redis channel layer

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

The inbox must reflect real backend state instantly, with no polling tricks or simulated events.

## Decision

Channels runs in the same ASGI process as HTTP (uvicorn). Domain services publish ids and small summaries to the `org.<id>` group **after the transaction commits**. The consumer authenticates via the session and checks membership plus `conversations.view`. Clients refetch over REST on events and on reconnect.

## Consequences

- (+) Clients never see rolled-back state; authorization stays in the REST layer.
- (-) Events are best-effort; a lost socket frame is repaired by refetch-on-reconnect.
