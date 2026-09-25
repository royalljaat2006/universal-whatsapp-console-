# ADR-009: Next.js feature modules with a thin typed API client

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

The UI must be productizable but not built ahead of the backend, and the browser must never touch Meta or secrets.

## Decision

Next.js 16 App Router, TypeScript, Tailwind v4, shadcn/ui. Feature folders (`features/*`), a typed fetch client mirroring the backend error contract, a session/organization store, and a WebSocket client with backoff. No data-caching library yet (`useApiQuery` suffices for phase 1); adopt TanStack Query when the inbox needs shared caches and optimistic updates. Sections without a backend render an explicit `NOT IMPLEMENTED` card.

## Consequences

- (+) Small dependency surface; honest UI.
- (-) Types are hand-written; generating them from `/api/v1/schema/` is a follow-up.
