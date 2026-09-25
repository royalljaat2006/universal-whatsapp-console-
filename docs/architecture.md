# Architecture

## Shape: modular monolith

One Django deployable with strict internal module boundaries. It is *not*
microservices (ADR-005). Every module owns its models and exposes a small
service interface. Other modules call those services, never each other's
internals. That keeps a later extraction into a separate service possible
if scale ever requires it.

```
                    ┌──────────────── nginx (single entrypoint) ────────────────┐
 Browser ──────────►│ /            → Next.js (UI only, no secrets)              │
 Browser ──────────►│ /api/v1      → Django REST (session auth, tenant-scoped)  │
 Browser ── WS ────►│ /ws/v1       → Django Channels (same ASGI process)        │
 Meta ─────────────►│ /webhooks/v1 → Django webhook ingestion (HMAC verified)   │
                    └────────────────────────────────────────────────────────────┘
                                   │                 │
                        PostgreSQL (state)    Redis (broker · cache · channel layer · rate limits)
                                   │                 │
                          Celery workers (webhooks · outbound · campaigns · sync · analytics · default)
                                   │
                        providers/whatsapp/meta ──► Meta Graph API
```

## Backend modules (`backend/apps/`)

| Module | Owns | Public interface |
|---|---|---|
| `identity` | User (global, email login), session auth API | `api.py` |
| `organizations` | Organization (tenant root), Membership, Role, Permission, tenant scoping primitives | `services.py`, `tenancy.py`, `api/scoping.py` |
| `businesses` | Business (workspace), BusinessAccess | `services.py` (`accessible_business_ids`, `get_business_for_membership`, `create_business`) |
| `teams` | Team, TeamMembership | `api.py` (list/create, members) |
| `audit` | AuditLog (append-only) | `services.record()` |
| `whatsapp` | ProviderCredential (secret *reference*), WhatsAppBusinessAccount, WhatsAppPhoneNumber, WhatsAppAccountEvent | `services.py` (`connect_business_account`, `sync_phone_numbers`, `sender_ref`), `account_events.handle_account_event()` |
| `contacts` | Contact, ContactIdentity (phone / BSUID), Tag (per business), ContactNote | `services.resolve_contact()`, `management_services.py` (edit, tags, notes) |
| `conversations` | Conversation (thread = number × contact), ConversationAssignment | `services.py`, `assignment.assign()` |
| `templates` | MessageTemplate, MessageTemplateVersion | `services.py` (sync, status events), `parameters.py` (describe/build) |
| `media` | MediaAsset, validation rules, object-storage wrapper | `services.store_upload/store_inbound`, `storage.py`, `rules.validate_media` |
| `messaging` | Message, MessageStatusEvent, MessageAttachment, state machine, send policy | `services/inbound.py`, `status.py`, `outbound.py`, `media_download.py`, `policy.evaluate()` |
| `campaigns` | Campaign, CampaignRecipient (schema) | — (next phases) |
| `billing` | PricingMarket, PricingRule, MessageCost (provider usage cost) | `pricing.quote()`, `services.price_message()` |
| `analytics` | MessageMetricDaily/Hourly (schema) | — |
| `notifications` | Notification (schema) | — |
| `webhooks` | WebhookEvent (raw), WebhookProcessingAttempt, processor | `ingest.py`, `processor.py` |
| `realtime` | WebSocket consumer, publish-after-commit | `publisher.publish_on_commit()` |

Shared kernel `backend/core/`: ids (UUIDv7), request context, structured
logging, middleware, error contract, secrets/crypto, rate limiter, health.
`core` never imports `apps`.

Provider layer `backend/providers/whatsapp/`: `base.py` (interfaces),
`types.py` (provider-neutral value objects), `meta/` (the only code that knows
Meta). Providers never import domain models.

**Dependency direction:** `api → services → models`; `webhooks → messaging /
templates / whatsapp services`; `messaging → contacts, conversations,
templates, whatsapp services`; everything → `core`; `whatsapp services →
providers`. There are no cycles.

Hierarchy: **Organization → Business (Kiosk, EPS, …) → WABA → Phone number**;
contacts, conversations and messages belong to a business (ADR-011).

## Key flows

**Inbound message** — Meta → `/webhooks/v1/meta/whatsapp/` → HMAC verify →
persist raw `WebhookEvent` (dedup on body hash) → commit → 200 → Celery
`webhooks` queue → processor → `MetaWebhookAdapter.parse()` → normalised
`InboundMessageEvent` → `messaging.inbound.ingest_inbound_message` (routes the
tenant by phone_number_id, resolves the contact, gets or creates the
conversation, inserts the message idempotently) → commit → publish
`message.created` / `conversation.updated` to `biz.<business_id>` → browser.

**Outbound message** — `POST /api/v1/messages/` → validate (number connected,
service window or approved template, idempotency key) → `Message(QUEUED)` →
commit → Celery `outbound` → claim under row lock (QUEUED → SENDING) → per-number
token bucket → provider call *outside* the transaction → ACCEPTED + wamid, or a
classified failure → status webhooks advance SENT → DELIVERED → READ.

**Status update** — webhook → processor → `apply_status_update` → append a
`MessageStatusEvent` (unique per message/status/timestamp) → state machine
(no regression) → publish `message.status_changed`.

Details: [webhook-architecture.md](webhook-architecture.md),
[messaging-architecture.md](messaging-architecture.md).

## Frontend (`frontend/`)

Next.js 16 App Router, TypeScript, Tailwind v4, shadcn/ui. Feature-oriented:

```
app/            routes only (thin): login, (console)/…
features/       auth, inbox, numbers, system-status, placeholder, registry.ts
components/     ui/ (shadcn primitives), layout/, status/
lib/api/        typed fetch client + ApiError (mirrors the backend error contract)
lib/realtime/   WebSocket client with backoff/heartbeat
services/       one file per backend resource
stores/         session + active organization + active business
hooks/          useApiQuery, useBusinessRealtime
types/          API contracts
```

The browser talks only to `/api/v1` and `/ws/v1` on the same origin. It never
calls Meta and never sees credentials. Sections without a backend show an
explicit `NOT IMPLEMENTED` / `NOT CONNECTED` card, never sample data.

## Decisions

See [adr/](adr/README.md).
