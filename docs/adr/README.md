# Architecture Decision Records

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-multi-tenancy.md) | Multi-tenancy with shared schema and organization scoping | Accepted |
| [ADR-002](ADR-002-provider-abstraction.md) | Provider abstraction for WhatsApp | Accepted |
| [ADR-003](ADR-003-event-driven-webhooks.md) | Event-driven webhook ingestion (persist, acknowledge, process async) | Accepted |
| [ADR-004](ADR-004-postgresql.md) | PostgreSQL as the system of record | Accepted |
| [ADR-005](ADR-005-redis-celery.md) | Modular monolith with Redis and Celery for async work | Accepted |
| [ADR-006](ADR-006-realtime.md) | Realtime via Django Channels and a Redis channel layer | Accepted |
| [ADR-007](ADR-007-session-auth.md) | Session authentication for the first-party web app | Accepted |
| [ADR-008](ADR-008-billing-domains.md) | Separate provider usage cost from SaaS billing | Accepted |
| [ADR-009](ADR-009-frontend.md) | Next.js feature modules with a thin typed API client | Accepted |
| [ADR-010](ADR-010-uuid7.md) | UUIDv7 primary keys | Accepted |
| [ADR-011](ADR-011-business-workspaces.md) | Businesses (workspaces) inside an organization | Accepted |

New decisions: copy the structure (Status, Date, Context, Decision, Consequences) and add a row here. Superseded ADRs stay, marked `Superseded by ADR-NNN`.
