# Database (PostgreSQL 17)

## Conventions

- **Primary keys:** UUIDv7 (`core/ids.py`). They are safe to expose (no
  enumeration) and time-ordered, so inserts into big tables append to the
  B-tree instead of splitting random pages the way UUIDv4 does. The exception
  is `Permission`, which uses its code as the natural key.
- `created_at` / `updated_at` on every table.
- **Tenancy:** every tenant-owned table has `organization_id` (FK PROTECT),
  even when it could be derived through a parent. This lets every query and
  index lead with the tenant and makes isolation checks trivial. Models that
  inherit a parent's tenant (`WhatsAppPhoneNumber`, `MessageTemplate`,
  `MessageAttachment`) copy it on save.
- JSONB for provider-shaped or schemaless data (template components, message
  content, raw webhooks, pricing snapshots, contact attributes).
- Provider vocabularies (template status, quality rating, pricing category)
  are stored **verbatim**. Internal enums exist only where our own logic
  branches (message status, direction).

## Entity overview

```
Organization ─┬─ Role ── Permission (global catalogue)
              ├─ Business (workspace: Kiosk, EPS, …) ── BusinessAccess ── Membership
              ├─ Membership ── User (global)   [business_scope: all | selected]
              ├─ Team ── TeamMembership ── Membership
              ├─ ProviderCredential
              ├─ WhatsAppBusinessAccount ─┬─ WhatsAppPhoneNumber
              │                            └─ MessageTemplate ── MessageTemplateVersion
              ├─ Contact ─┬─ ContactIdentity (phone | bsuid | parent_bsuid)
              │           └─ Tag (M2M, shared with Conversation)
              ├─ Conversation (phone_number × contact)
              │     └─ Message ─┬─ MessageStatusEvent (append-only)
              │                 ├─ MessageAttachment (metadata; blob in object storage)
              │                 └─ MessageCost (1:1, immutable)
              ├─ Campaign ── CampaignRecipient ── Message (1:1)
              ├─ MessageMetricDaily / MessageMetricHourly
              ├─ Notification
              └─ AuditLog (org nullable for platform events)
PricingMarket (effective-dated calling-code → market map)
PricingRule (org / business / WABA nullable = platform rate card; volume tiers)
Cycle 3: Audience (saved segment) · Campaign.business (NOT NULL) + lifecycle/heartbeat fields
         CampaignRecipient (snapshot: address, resolved variables, attempts, failure category)
         CampaignEvent (append-only) · ExportJob (reports)
         MessageMetricDaily/Hourly (business, WABA, number, template?, campaign?; local days)
         CostMetricDaily (… category, currency) · ContactActivityDaily (distinct counts)
         AnalyticsDirtyBucket (business, UTC date) · Message indexes (business, occurred_at),
         (campaign, status) built CONCURRENTLY
Cycle 2: MediaAsset (org, business; bytes in object storage)
         ContactNote (contact) · Tag.business (NOT NULL, unique lower(name) per business)
         ConversationAssignment (history) · WhatsAppAccountEvent (dedupe_key unique)
         Message.business_account / campaign / cost_status · MessageCost.pricing_snapshot
WebhookEvent ── WebhookProcessingAttempt   (not tenant-owned; organization_ids[])
```

## Business dimension (Build Cycle 1, ADR-011)

Organization → **Business** → WABA → PhoneNumber. `business_id` is
denormalised (NOT NULL, FK PROTECT) onto `WhatsAppBusinessAccount`,
`WhatsAppPhoneNumber`, `MessageTemplate`, `Contact`, `ContactIdentity`,
`Conversation` and `Message`. Child models copy it from their parent on save
(number ← WABA, template ← WABA, conversation ← number, message ←
conversation), so the chain cannot disagree. Contacts are per business: the
identity key is now `(business, kind, scope, value)`.

Migration strategy: add nullable columns → `businesses.0002` backfills a
per-organization "Default" business and propagates it down the chain →
`ALTER … SET NOT NULL`. Assumption (pre-release data only): a contact that
spoke to several businesses was assigned to the business of its first
conversation.

New WABA columns: `connection_state` (pending/connected/error/disconnected),
`connection_error[_code]`, `last_verified_at`, `sync_state`
(never/syncing/succeeded/failed), `sync_error`, `sync_started_at`, verified
provider attributes (`account_review_status`, `business_verification_status`,
`country`, `ownership_type`, `message_template_namespace`) and local
`metadata`. Phone numbers gain local `label`, `metadata` and `last_error`.
Messages gain `provider_metadata` (the raw inbound message object, or the
provider's send response). Conversations gain the `archived` status,
`last_message_direction` and `status_changed_at`. Contacts gain `notes`,
`metadata`, `last_message_at` and `last_inbound_at`.

## Entities considered and folded in

| Proposed | Decision | Why |
|---|---|---|
| ContactAttribute | JSONB `Contact.attributes` | Read and written as a whole per contact. An EAV table adds joins without a query need. A GIN index can come when segments query it |
| ConversationParticipant | not created | WhatsApp threads here are 1:1 (number × contact). Revisit if the Groups API is adopted |
| ConversationAssignment | fields on Conversation (+ AuditLog history) | The current assignee is what the inbox queries. History is covered by audit events |
| MessageError | fields on Message + `MessageStatusEvent.errors` | Errors are per status report and are already stored there |
| TemplateComponent / TemplateVariable | JSONB `components` | Meta's component schema is rich and changes between versions. It's always read whole |
| CampaignAudience / CampaignMessage | `Campaign.audience_definition` JSONB / `CampaignRecipient.message` | The definition is read with its campaign, and the recipient *is* the per-message record |
| BusinessProfile | JSONB on WhatsAppPhoneNumber | A synced snapshot, not independently queried |
| WhatsAppConnection | `WhatsAppBusinessAccount` + `ProviderCredential` | The WABA *is* the connection. The credential is a separate row so tokens can be rotated or shared |
| UsageRecord / BillingPeriod | deferred | They belong to SaaS subscription billing, a separate domain (ADR-008) |
| TemplateMetricDaily / NumberMetricDaily / CampaignMetricDaily / CostMetricDaily | one `MessageMetricDaily` with nullable template/campaign dimensions | Same counters, different grouping. One upserted table avoids four drifting copies |

## Integrity constraints (idempotency lives here)

| Constraint | Guarantees |
|---|---|
| `identity_user_email_ci_unique` (`lower(email)`) + `unique(email)` | one account per email, case-insensitive |
| `membership_org_user_unique` | one membership per user per org |
| `waba_provider_external_unique`, `phone_provider_external_unique` | a WABA or number belongs to exactly one tenant (webhook routing is unambiguous) |
| `credential_exactly_one_secret_source` (CHECK) | a credential is either a reference or ciphertext, never both |
| `contact_identity_business_unique (business, kind, scope, value)` | concurrent webhooks resolve to one contact per business |
| `business_org_slug_unique`, `business_access_unique` | businesses and grants are unique per organization/membership |
| `conversation_thread_unique (org, phone_number, contact)` | one thread per pair |
| `message_provider_id_unique (phone_number, direction, external_id) WHERE external_id IS NOT NULL` | duplicate webhook deliveries never create duplicate messages. It is scoped per sender *and direction* because two of our own numbers messaging each other see the same wamid as outbound on one and inbound on the other |
| `message_idempotency_key_unique (org, key) WHERE key IS NOT NULL` | client retries of `POST /messages` create one message |
| `message_status_event_unique (message, provider_status, occurred_at)` | replayed or duplicated status reports are recorded once |
| `webhook_event_body_unique (provider, body_sha256)` | byte-identical redeliveries are stored once |
| `template_waba_name_lang_unique`, partial `template_waba_external_unique` | template upserts are idempotent |
| `campaign_recipient_unique (campaign, contact)` | a contact gets a campaign once, even if the audience build is retried |
| `metric_*_unique … NULLS NOT DISTINCT` | rollup upserts are idempotent even with null dimensions |
| `pricing_rate_non_negative`, `pricing_valid_period` (CHECK) | sane rate cards |

## Indexes and the queries they serve

| Index | Query |
|---|---|
| `msg_org_conv_occurred_idx (org, conversation, -occurred_at)` | conversation timeline (the hottest read) |
| `msg_org_created_idx`, `msg_org_phone_created_idx`, `msg_org_status_created_idx` | tenant/number/status message lists, failure dashboards |
| `msg_sending_partial_idx (sending_at) WHERE status='sending'` | crash sweeper. The partial index stays tiny |
| `conv_business_last_msg_idx`, `conv_business_status_last_idx`, `conv_phone_last_msg_idx` | inbox lists inside a business / number, sorted by activity |
| `conv_org_last_msg_idx` | organization-wide inbox for all-business members |
| `phone_business_status_idx` | number selector per business |
| `conv_assignee_status_idx` | "my open conversations" |
| `membership_user_status_idx` | tenant resolution on every request |
| `contact_business_phone_idx`, `contact_business_created_idx` | contact search / list per business |
| `webhook_due_partial_idx (next_attempt_at) WHERE status IN (pending, failed)` | outbox sweeper |
| `webhook_received_idx`, `webhook_status_received_idx` | ops browsing / replay selection |
| `audit_org_created_idx`, `audit_org_resource_idx` | audit screen, "history of this resource" |
| `pricing_lookup_idx` | cost calculation |
| `campaign_due_partial_idx`, `campaign_recipient_status_idx` | scheduler, batch selection |

Unique constraints double as indexes for provider-id lookups (for example,
status webhook → message).

## Scaling path (when volumes justify it, not before)

1. Monthly range partitioning of `messaging_message`, `messaging_messagestatusevent`
   and `webhooks_webhookevent` on created/received time. UUIDv7 keeps
   partition pruning effective.
2. Read replica for analytics and exports.
3. PostgreSQL row-level security on `organization_id` as defence in depth
   (ADR-001).

## Retention

Nothing is deleted automatically (`RETENTION_ENABLED=false`). Policy knobs
exist per class of data. See [security.md](security.md#data-retention).
