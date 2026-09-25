# Messaging architecture

## Message model

- `direction`: `inbound | outbound`
- `message_type`: `text, template, image, video, audio, document, sticker,
  interactive, button, reaction, location, contacts, order, system,
  unsupported`
- `status`: internal, provider-independent (below). Inbound messages are
  `received`.
- `external_id`: provider message id (Meta `wamid…`), unique per
  (sender number, direction). NULL until the provider accepts the message.
- Per-status timestamps (`accepted_at`, `sent_at`, `delivered_at`, `read_at`,
  `failed_at`) are filled even when an out-of-order event does not change the
  status.
- `billable`, `pricing_category`, `pricing_type`: copied from the provider's
  pricing report. Costs themselves live in `billing.MessageCost`.

<a id="state-transitions"></a>
## State machine (`apps/messaging/state.py`)

```
QUEUED ─► SENDING ─► ACCEPTED ─► SENT ─► DELIVERED ─► READ
   │         │           │         │
   └─────────┴───────────┴─────────┴──► FAILED ──(delivery evidence)──► DELIVERED / READ
```

| Rule | Reason |
|---|---|
| Progress states only move forward by rank | late `delivered` after `read` must not regress |
| FAILED accepted only from QUEUED/SENDING/ACCEPTED/SENT | a delivered message cannot "un-deliver" |
| DELIVERED/READ override FAILED | the recipient demonstrably got it |
| `played` (voice) maps to READ | raw value kept in `MessageStatusEvent.provider_status` |
| Unknown provider statuses are recorded, not applied | vocabulary can grow without breaking |

Every report is appended to `MessageStatusEvent` with `applied` true or false,
which gives a complete audit trail of what the provider said and when.
Transitions run under `SELECT … FOR UPDATE` on the message row.

## Outbound pipeline

1. **Accept (HTTP, synchronous):** `POST /api/v1/messages/`
   - Permission `messages.send`, throttle scope `send`.
   - The number must be `connected`. A free-form message requires an open
     24h customer service window (`conversation.last_inbound_at`). A template
     must exist for the WABA/language and be `APPROVED`.
   - `Idempotency-Key` header: the same key with the same request fingerprint
     returns the existing message (200, `Idempotent-Replayed: true`). The same
     key with a different payload → 409 `IDEMPOTENCY_KEY_REUSED`.
   - Persists `QUEUED` and returns **202**. The provider is called by a worker.
2. **Dispatch (worker, `outbound` queue):**
   - Claim under a row lock: only `QUEUED` → `SENDING`. A duplicated task is a
     no-op.
   - Per-number Redis token bucket (`throughput_mps`, default 80/s). When
     empty, the task is rescheduled without calling Meta.
   - Provider call *outside* any DB transaction.
   - Success → `external_id` stored, `ACCEPTED`.
3. **Failure policy** (`providers/whatsapp/errors.Retryability`):

| Class | Example | Action |
|---|---|---|
| SAFE | connect error, 429/130429/131056/80007 | back to QUEUED, retry with jittered exponential backoff (max 6 attempts) |
| TRANSIENT | 131000, 131016, 5xx on GET | retry |
| AMBIGUOUS | read timeout after sending, 5xx on POST | **FAILED `PROVIDER_OUTCOME_UNKNOWN`, never auto-retried** (Meta has no idempotency key; a retry could double-send) |
| PERMANENT | 131026, 131047, 132xxx, auth | FAILED with the provider code in `error_details` |
| Missing credential | env secret absent | FAILED `CONFIGURATION_REQUIRED` |

4. **Crash safety:** `sweep_stuck_sending_messages` (every 5 min) fails
   messages left in `SENDING` longer than `MESSAGE_SENDING_STUCK_SECONDS`
   as `PROVIDER_OUTCOME_UNKNOWN`. It fails them for review and does not resend.

<a id="campaigns"></a>
## Campaigns (Build Cycle 3)

Implemented; see [campaign-architecture.md](campaign-architecture.md). A
campaign message is an ordinary outbound `Message` (with `campaign` set)
created by `enqueue_outbound_message` with the idempotency key
`campaign:<campaign>:<recipient>:<attempt>`. It is dispatched on the
`campaigns` queue and throttled by the campaign rate and the per-number
bucket. It is never sent inside an HTTP request. The number's messaging-limit
tier is checked at launch (warning), and WhatsApp enforces the portfolio
limit itself (the excess fails with a provider error, which is recorded).

## Derived-data hook

Every message create, status move, late timestamp and cost outcome calls
`apps/messaging/services/hooks.message_changed()` inside the same
transaction. It marks the analytics bucket dirty and mirrors the status onto
the campaign recipient.

## Send policy (Cycle 2, `apps/messaging/policy.py`)

`evaluate(conversation)` returns `SendPolicy(free_form_allowed,
template_allowed, window_open, window_expires_at, reason)`, where reason ∈
`SERVICE_WINDOW_OPEN | TEMPLATE_REQUIRED_WINDOW_CLOSED |
TEMPLATE_REQUIRED_NO_CUSTOMER_MESSAGE`. `send_message()` calls it for every
send. A free-form send outside the window raises `422
SERVICE_WINDOW_CLOSED` with `details.policy`. The same object is serialised
on each conversation (`send_policy`), and the Inbox composer only mirrors it.

## Content types

| `type` | Prepared by | Notes |
|---|---|---|
| `text` | `TextContent` | ≤ 4096 chars |
| `template` | `TemplateSend(template_id, parameters)` | template must be `APPROVED`, not `missing_from_provider`, same WABA as the number; components built by `apps/templates/parameters.build` |
| `media` | `MediaSend(media_asset_id, caption, filename)` | asset must belong to the same business; captions only for image/video/document |

Failures store a user-safe `error_message` from
`services/error_catalog.py` (verified Meta codes → text). The raw provider
message is kept in `error_details` for operators.

## Media

See [media-architecture.md](media-architecture.md). Inbound media is
downloaded asynchronously into object storage, and outbound media is uploaded
to Meta at dispatch with the provider media id cached for 29 days. Blobs never
go into PostgreSQL.

## Rate limiting summary

| Level | Mechanism | Status |
|---|---|---|
| API per user / anon | DRF throttles (`1200/min`, `60/min`) | on |
| Login | scoped `auth` 10/min | on |
| Send endpoint | scoped `send` 600/min per user | on |
| Per phone number (provider throughput) | Redis token bucket | on |
| Webhook ingress | nginx `limit_req` 50 r/s, burst 200 per IP | on |
| Per organization / per campaign | campaign cap + org quotas | campaign phase |
| Provider throttling feedback | error-code classification → backoff | on |

Limits are env-configurable (`THROTTLE_*`). The numbers above are starting
points to tune with real traffic, not Meta rules. Meta rules are listed in
`integrations/meta/api-assumptions.md`.
