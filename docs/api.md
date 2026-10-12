# API conventions

- Versioned under `/api/v1/`. The OpenAPI 3 schema is at `GET /api/v1/schema/`
  (authenticated). Swagger UI at `/api/v1/docs/` in DEBUG only.
- JSON only. Session auth (cookie) and CSRF (`X-CSRFToken`) on unsafe methods.
- Tenant-scoped endpoints require the `X-Organization-ID: <uuid>` header.
- Business-owned resources accept an optional `X-Business-ID: <uuid>` header
  that narrows results to one business (workspace). The server validates it
  against the caller's business access (404 if not accessible). Without it,
  results still include only businesses the caller may access.
- Serializers are explicit contracts (no `fields="__all__"`). Models are never
  exposed directly.
- Pagination: cursor-based (`?cursor=…&page_size=…`, max 200) → `{next,
  previous, results}`. Stable under concurrent inserts.
- Every response carries `X-Request-ID` (accepted from upstream if well-formed).

## Error contract

```json
{"error": {"code": "SERVICE_WINDOW_CLOSED",
           "message": "Free-form messages require an open customer service window …",
           "details": {},
           "request_id": "3f2c…"}}
```

| HTTP | code examples |
|---|---|
| 400 | `VALIDATION_ERROR` (`details.fields`), `MALFORMED_REQUEST`, `ORGANIZATION_REQUIRED` |
| 401 | `AUTHENTICATION_REQUIRED`, `INVALID_CREDENTIALS` |
| 403 | `PERMISSION_DENIED` |
| 404 | `NOT_FOUND` (also for foreign-tenant ids) |
| 409 | `IDEMPOTENCY_KEY_REUSED`, `CONFLICT` |
| 422 | `SERVICE_WINDOW_CLOSED`, `TEMPLATE_NOT_SENDABLE`, `PHONE_NUMBER_NOT_READY` |
| 429 | `RATE_LIMITED` (+ `Retry-After`) |
| 500 | `INTERNAL_ERROR` (stack traces are logged, never returned) |
| 503 | `CONFIGURATION_REQUIRED` |

## Endpoints (Build Cycle 1)

"B" = business-scoped (honours `X-Business-ID`, always restricted to accessible businesses).

| Method & path | Permission | Notes |
|---|---|---|
| `GET /api/v1/auth/csrf/` | — | sets the CSRF cookie |
| `POST /api/v1/auth/login/` | — | `{email, password}`; CSRF enforced; throttled. Returns the user, or `{"mfa_required": true}` when a second step is needed |
| `POST /api/v1/auth/mfa/verify/` | pending sign-in | `{code}` or `{recovery_code}`; completes sign-in |
| `GET /api/v1/auth/mfa/` | authenticated | `{enabled, recovery_codes_remaining, required_for_staff}` |
| `POST /api/v1/auth/mfa/setup/` | authenticated | `{password}` -> `{secret, otpauth_uri}` (shown once) |
| `POST /api/v1/auth/mfa/confirm/` | authenticated | `{code}` -> `{recovery_codes}` (shown once); turns MFA on |
| `POST /api/v1/auth/mfa/disable/` | authenticated | `{password, code or recovery_code}` |
| `POST /api/v1/auth/mfa/recovery-codes/` | authenticated | `{password, code}` -> new `{recovery_codes}` |
| `POST /api/v1/auth/logout/` | authenticated | |
| `GET /api/v1/auth/me/` | authenticated | |
| `GET /api/v1/organizations/` | authenticated | your memberships + permission codes (no org header) |
| `GET /api/v1/organizations/current/` | `organization.view` | |
| `GET /api/v1/organizations/current/members/` | `members.view` | |
| `GET /api/v1/businesses/` | `organization.view` | accessible businesses (unpaginated), with WABA/number counts |
| `POST /api/v1/businesses/` | `businesses.manage` | `{name, slug?, description?}` → 201; duplicate slug → 409 |
| `GET/PATCH /api/v1/businesses/<id>/` | view / `businesses.manage` | name, description, status, timezone, metadata |
| `GET /api/v1/whatsapp/credentials/` | `integrations.manage` | id, label, backend (`env`/`encrypted`), fingerprint — never the secret |
| `POST /api/v1/whatsapp/credentials/` | `integrations.manage` | `{label, secret_reference}` (`env:PROVIDER_SECRET_*`) **or** `{label, access_token}` (encrypted at rest, write-only) |
| `GET /api/v1/whatsapp/accounts/[<id>/]` | `numbers.view` | B; connection_state, sync_state, errors, webhook subscription |
| `POST /api/v1/whatsapp/accounts/` | `numbers.manage` | `{business_id, external_id, credential_id, name?, business_portfolio_external_id?}` → **202**; verify → subscribe → sync queued |
| `PATCH /api/v1/whatsapp/accounts/<id>/` | `numbers.manage` | name, credential_id, portfolio id, metadata |
| `POST /api/v1/whatsapp/accounts/<id>/verify/` | `numbers.manage` | 202; queues a connection check |
| `POST /api/v1/whatsapp/accounts/<id>/sync/` | `numbers.manage` | 202; queues number + template sync |
| `GET /api/v1/whatsapp/numbers/[<id>/]` | `numbers.view` | B; filters `business_account`, `status`; includes WABA connection/sync state |
| `PATCH /api/v1/whatsapp/numbers/<id>/` | `numbers.manage` | local fields only: `label`, `throughput_mps`, `metadata` |
| `GET /api/v1/contacts/[<id>/]` | `contacts.view` | B; `search` |
| `GET /api/v1/conversations/[<id>/]` | `conversations.view` | B; filters `phone_number`, `status` (archived hidden unless requested), `assigned=me`; ordered by last activity |
| `PATCH /api/v1/conversations/<id>/` | `conversations.assign` | `{status: open\|pending\|resolved\|archived}` |
| `POST /api/v1/conversations/<id>/read/` | `conversations.view` | resets unread count |
| `GET /api/v1/messages/?conversation=<id>` | `conversations.view` | B; timeline (newest first) |
| `POST /api/v1/messages/` | `messages.send` | see below |
| `GET /api/v1/messages/<id>/` | `conversations.view` | B |
| `GET /api/v1/templates/[<id>/]` | `templates.view` | B; filters `business_account`, `status` |
| `GET /api/v1/audit-logs/` | `audit.view` | organization-level; `action` filter |

Outside `/api`: `GET|POST /webhooks/v1/meta/whatsapp/` (Meta),
`GET /health/live`, `GET /health/ready`, `GET /health/dependencies`
(staff only), WebSocket `/ws/v1/realtime/?organization=<uuid>&business=<uuid>`.

### Send a message

```http
POST /api/v1/messages/
X-Organization-ID: 0199…
Idempotency-Key: order-8812-shipped
Content-Type: application/json

{"phone_number_id": "0199…", "to": "919876543210",
 "type": "template",
 "template": {"template_id": "0199…",
              "parameters": {"header": {"media_asset_id": "0199…"},
                             "body": {"1": "Rajesh", "2": "ORD-8812"},
                             "buttons": {"0": "orders/8812"}}}}
```

Templates are referenced by **id** (synced, `APPROVED`, same WABA as the
sending number). The backend builds Meta's `components`. Invalid values
return `422 TEMPLATE_PARAMETERS_INVALID` with `details.fields`
(`header.text`, `header.media_asset_id`, `body.<key>`, `buttons.<index>`).
Media: `{"type": "media", "media": {"media_asset_id", "caption?", "filename?"}}`
after `POST /api/v1/media/`.

`202` → the message in `queued` (or `200` + `Idempotent-Replayed: true` on a
replay). `to` may be replaced by `recipient_bsuid`. `type: "text"` needs
`text: {body, preview_url?}` and an open service window.

Reply inside an existing conversation (what the Inbox uses). The number and
recipient come from the conversation, which must be in an accessible business:

```json
{"conversation_id": "0199…", "type": "text", "text": {"body": "Yes, it comes in blue."}}
```

## Endpoints added in Build Cycle 2

| Method & path | Permission | Notes |
|---|---|---|
| `GET /api/v1/conversations/` | `conversations.view` | B; filters `phone_number, status, unread=1, assigned=me\|none\|<membership>, team, tag, date_from, date_to` (date = whole local day), `q` (name, phone digits, id, message text); cursor-paginated. Each row has `assignee, team, tags, send_policy` |
| `POST /api/v1/conversations/<id>/assign/` | `conversations.assign` | `{membership_id?, team_id?}`; assignee must be active with access to the business, else `422 INVALID_ASSIGNEE` |
| `GET /api/v1/conversations/<id>/assignments/` | `conversations.view` | assignment history |
| `GET /api/v1/conversations/assignees/` | `conversations.view` | members with access to the selected business + teams |
| `POST /api/v1/conversations/<id>/tags/`, `DELETE …/tags/<tag_id>/` | `messages.send` | tag must belong to the conversation's business |
| `GET/PATCH /api/v1/contacts/<id>/` | view / `contacts.manage` | editable: `display_name, attributes, opt_in_status, opt_in_source` |
| `GET/POST /api/v1/contacts/<id>/notes/`, `DELETE …/notes/<id>/` | view / `contacts.manage` | internal notes |
| `POST /api/v1/contacts/<id>/tags/`, `DELETE …/tags/<tag_id>/` | `contacts.manage` | |
| `GET /api/v1/contacts/<id>/history/` | `contacts.view` | the contact's conversations |
| `GET/POST /api/v1/tags/`, `PATCH/DELETE /api/v1/tags/<id>/` | view / `contacts.manage` | business-scoped; POST needs `X-Business-ID`; names unique ignoring case (409) |
| `GET /api/v1/templates/` | `templates.view` | B; `q, status, category, language, business_account, sendable=1` |
| `GET /api/v1/templates/<id>/` | `templates.view` | adds `components` + `structure` (variables to fill) |
| `POST /api/v1/templates/sync/` | `templates.manage` | 202; queues template sync for the business's WABAs |
| `POST /api/v1/media/` | `messages.send` | multipart `file`; needs `X-Business-ID`; `422 MEDIA_REJECTED` with `details.reason` |
| `GET /api/v1/media/<id>/`, `GET …/content/` | `conversations.view` | B; content is streamed with a sandbox CSP |
| `GET /api/v1/messages/<id>/status-history/` | `conversations.view` | every status webhook applied or ignored |
| `GET /api/v1/whatsapp/events/` | `numbers.view` | B; account-level webhook events |
| `GET/POST /api/v1/businesses/<id>/access/`, `DELETE …/access/<membership>/` | `members.manage` | grant/revoke; self and owner changes → `403 ACCESS_CHANGE_FORBIDDEN` |
| `PATCH /api/v1/organizations/current/members/<id>/` | `members.manage` | `{business_scope: all\|selected}` |
| `GET/POST /api/v1/teams/`, `POST/DELETE /api/v1/teams/<id>/members/[<membership>/]` | `teams.view` / `teams.manage` | |

## Endpoints added in Build Cycle 3

B = business-scoped. Writes use the validated `X-Business-ID`; ids in bodies
must belong to that business (unknown or foreign ids give 404).

| Method & path | Permission | Notes |
|---|---|---|
| `GET /api/v1/analytics/overview/` | `analytics.view` | B; `date_from, date_to` (business-local days, default last 7), `business_account, phone_number, template, campaign, language`. Totals, rates (`null` = no data), unique contacts, active and resolved conversations, cost state, freshness |
| `GET /api/v1/analytics/timeseries/` | `analytics.view` | `granularity=hour` (≤ 7 days) or `day`; zero-filled buckets plus per-day cost |
| `GET /api/v1/analytics/numbers/` | `analytics.view` | per number: counters, rates, contacts, templates used, cost, quality and limit tier |
| `GET /api/v1/analytics/templates/` | `analytics.view` | per template: sends, outcomes, unique recipients, first/last used, cost |
| `GET /api/v1/analytics/costs/` | `analytics.view` | `group_by=business\|business_account\|phone_number\|template\|campaign\|category\|date` |
| `GET /api/v1/analytics/failures/` | `analytics.view` | failures grouped by cause, plus the 50 most recent |
| `GET/POST /api/v1/campaigns/` | `campaigns.view` / `campaigns.manage` | list (`status`, `q`; counters without N+1) / create draft |
| `GET/PATCH/DELETE /api/v1/campaigns/<id>/` | view / manage | edit and delete only while `draft` (else 409) |
| `GET /api/v1/campaigns/<id>/preview/` | `campaigns.view` | launch checks, summary and audience preview |
| `POST /api/v1/campaigns/<id>/launch/` | `campaigns.launch` | `{"confirm": true}` required; 422 `CAMPAIGN_NOT_LAUNCHABLE` with the failed checks |
| `POST /api/v1/campaigns/<id>/pause/`, `resume/`, `cancel/`, `retry-failed/` | `campaigns.launch` | state rules enforced server-side (409 `CAMPAIGN_STATE_CONFLICT`) |
| `GET /api/v1/campaigns/<id>/report/`, `failures/`, `events/` | `campaigns.view` | persisted counters, cost, timing, grouped failures, lifecycle log |
| `GET /api/v1/campaigns/<id>/recipients/` | `campaigns.view` | cursor pagination; `status`, `failure_category` |
| `POST /api/v1/campaigns/audience-preview/` | `campaigns.view` | `{"definition"}` → exact count + sample |
| `GET/POST /api/v1/audiences/`, `GET/PATCH/DELETE /api/v1/audiences/<id>/` | view / manage | saved segments (unique name per business) |
| `POST /api/v1/exports/` | `reports.export` + data permission | `{"kind", "params"}` → 202; async CSV job |
| `GET /api/v1/exports/[<id>/]`, `GET /api/v1/exports/<id>/download/` | `reports.export` | status / streamed CSV |

<a id="exports"></a>
### Exports

Kinds and their required data permission:

| Kind | Params | Data permission |
|---|---|---|
| `campaign_recipients` | `campaign_id` | `campaigns.view` |
| `campaign_summary` | `campaign_id` | `campaigns.view` |
| `messages` | `date_from, date_to[, phone_number_id, direction, campaign_id]` | `conversations.view` |
| `costs` | `date_from, date_to` | `billing.view` |
| `analytics_daily` | `date_from, date_to` | `analytics.view` |

Workers stream rows from chunked iterators into object storage (`exports/…`).
Cells beginning with `= + - @` are prefixed with `'` (CSV injection).
Requests and downloads are audited.

## Template Studio

Rules live in `apps/templates/studio.py` (pure checks), `studio_service.py`
(drafts, confirmed submit, reconcile) and `guidance.py` (plain status). The
limits are from Meta's template documentation (read 2026-10-12): name lowercase
letters/digits/underscores up to 512; header text up to 60 with one variable;
body up to 1024; footer up to 60 and plain text; every variable needs an example;
positional variables numbered from 1 with no gaps, named ones lowercase and
underscores; a body cannot start or end with a variable; up to 10 buttons (quick
replies first, then website, then call; button text up to 25; at most 2 website
and 1 call button); the same body and footer as an existing template is rejected;
100 creations per account per hour. Supported: MARKETING and UTILITY, a text
header, body, footer, quick-reply / website / call buttons. Not supported (stated
in the API, not hidden): AUTHENTICATION templates, media or location headers,
copy-code and other buttons. Content review (policy, spam, category fit) is
Meta's; the console cannot predict it.

- `GET /api/v1/templates/studio/options/` (`templates.view`): categories, languages,
  limits, starters (editable examples, not approved templates), what is unsupported.
- `POST /api/v1/templates/studio/validate/` (`templates.manage`): errors, warnings,
  counts, preview and the components that would be sent. Saves and sends nothing.
- `GET|POST /api/v1/templates/drafts/`, `GET|PATCH|DELETE .../drafts/<id>/`: local
  drafts, scoped to the caller's businesses (foreign or out-of-scope ids are 404).
  A draft response carries live `checks`, a `content_hash` and `guidance`.
- `POST .../drafts/<id>/submit/` `{confirm: true, content_hash}`: the only call that
  sends a template to Meta. `confirm` must be true and the hash must equal the hash
  of the draft as it is now (what was reviewed is what is sent). The draft row is
  locked and moved `draft -> submitting` in a committed transaction before the
  provider call, so a repeated or concurrent submit is refused; a unique constraint
  backs this up. Meta refusing leaves an editable draft with Meta's reason. An
  unknown outcome (timeout) makes the draft `unconfirmed`: it cannot be re-sent
  until a sync completed after the attempt shows whether Meta has it.
- `POST .../drafts/<id>/check/` queues a template sync ("Check with Meta").
- `GET /api/v1/templates/<id>/guidance/`: `state` (draft | submitted | pending |
  approved | rejected | paused | disabled | unknown), Meta's status verbatim,
  `verified_by_meta` (the status was reported by a sync or webhook AFTER the submit,
  not just returned by the create request), plain explanation and next step, and for
  a rejection Meta's reason code verbatim with meaning and how to fix. `can_send`
  requires approved AND verified. An unrecognised status is shown verbatim and is
  never sendable.
- `POST .../templates/<id>/copy-to-draft/`: a corrected copy of a rejected template as
  a new draft under a new name (`_v2`, `_v3`). Sends nothing.

Audit: `template.draft_saved`, `template.draft_deleted`, `template.submit_requested`,
`template.submitted`, `template.submit_failed`. Metadata holds ids, name, language,
category, hash prefix and error codes only: never the message text, examples or
credentials. Drafts live in `templates_templatedraft` (migration 0005, with the
row-level-security policy in core migration 0004).

## WhatsApp setup and the verified test message

Rules live in `apps/whatsapp/onboarding.py` and `test_message.py`.

- `GET /api/v1/whatsapp/setup/` (`numbers.view`, scoped to the selected business
  or every business the caller may access): readiness derived only from stored
  evidence; never calls Meta. Returns `overall` (`not_started|in_progress|blocked|ready`),
  `summary`, `next_step`, ordered `steps` and a `server` check. Each step has
  `state` (`verified|pending|failed|unsupported|unknown`), plain `detail`/`next_step`,
  `fix_in` (`console|meta|administrator|none`), `action` and `evidence`. Steps:
  connection, numbers, webhook_subscription (what Meta reports), webhook_delivery
  (verified only by a signed event that was stored for this account; an unsigned
  request is rejected and is not evidence), templates, test_message (from real
  message status). `capabilities` states openly what is unsupported (Embedded
  Signup not configured; the console cannot read the callback URL saved in the
  Meta app). `ready` requires connection, numbers, webhook_subscription and a
  delivered test message.
- `POST /api/v1/whatsapp/embedded-signup/session/` `{business_id}` (`numbers.manage`)
  returns a one-time `state` (cache, 15 min, bound to user, organization and
  business). `embedded-signup/complete/` now requires it; it is burned on first use,
  so forged, replayed, cross-user and cross-business completions are refused
  before any Meta call.
- `POST /whatsapp/test-message/preview/` (`messages.send`) `{phone_number_id,
  contact_id | to, attest_consent?, template_id?}` returns exactly what would be
  sent (sender, recipient, content, template, cost, restrictions, blockers) and,
  only when sendable, a single-use `approval.token` (10 min). It sends nothing.
  Content is chosen by the server: an approved template with no variables.
  The recipient must be an opted-in contact or a number the user attests agreed
  (opted-out contacts are refused).
- `POST /whatsapp/test-message/send/` `{approval_token, approve: true}` is the
  separate gate. The preview is rebuilt from live data and must match; the send
  goes through `enqueue_outbound_message` (scope, number-connected, template
  rules, queue, rate limit, audit) with idempotency key `testmsg:<hash(token)>`.
  Response says queued only; read `GET /messages/<id>/` for the provider-reported
  status. `POST .../cancel/` discards an approval. Audit: `whatsapp.test_message_approved`
  and `_cancelled` (ids and categories only).

## Failed-message recovery

Read endpoints need `conversations.view`; retry needs `messages.send`. All are
tenant- and business-scoped (a foreign or out-of-scope id is 404). Rules live in
`apps/messaging/services/recovery.py` and reuse `failure_categories`.

- `GET /api/v1/messages/failures/?days=7&limit=25` (days 1-30, limit up to 50):
  `{ total, shown, kinds: {retryable|not_retryable|unknown|external: n}, results: [...] }`.
  Each result: recipient, sender number, short preview, `failed_at`, and
  `recovery { category, label, kind, explanation, next_step, provider_code,
  safe_error, can_retry, blockers[{code,message}], links[{label,url}],
  retried_as }`. `safe_error` is trimmed and token-like text is hidden.
  `retried_as` is the retry message's own stored status (never inferred).
- `GET /api/v1/messages/<id>/recovery/`: the same plus `retry_preview`
  (only when a retry is allowed): destination, message text, duplicate-risk
  statement and `cost` (`free` | `estimate` from the loaded rate card | `unknown`).
- `POST /api/v1/messages/<id>/retry/` `{"confirm": true}`: queues ONE retry
  (202, `status_note` says "accepted for sending", not delivered). Refused with
  409 `RETRY_NOT_ALLOWED` and `details.blockers` otherwise.

A retry is allowed only when the message is a failed, outbound, non-campaign
message whose category is in `failure_categories.RETRYABLE` (rate limit,
authentication, temporary), is under 7 days old, has not been retried, has no
later identical non-failed message, and its number is connected. Unknown,
timeout (outcome unknown), invalid-recipient, template, policy and Meta-only
failures are never retried. Duplicate protection: the retry uses the key
`retry:<original id>`, so the database's unique (organization, idempotency_key)
constraint makes a second retry impossible even under concurrent requests.
Audit: `message.retry_requested`, `message.retry_refused`, and
`message.retry_outcome` (written when the retry is delivered or fails); only ids,
category and code are recorded. Official destinations returned for Meta-only
fixes: WhatsApp Manager and Meta's error-code reference.

## WhatsApp connection health

`GET /api/v1/whatsapp/health/` (`numbers.view`; scoped to the selected business,
or every business the caller may access): plain-language health of the
WhatsApp accounts and numbers. Read-only and derived only from stored signals
(`apps/whatsapp/health.py`); it never calls Meta in the request and never
returns tokens or provider ids.

```
{ generated_at, overall: healthy|attention|problem|unknown, summary,
  accounts: [{ id, name, business_id, business_name, overall, last_checked_at, checks: [...] }],
  numbers:  [{ id, label, display_phone_number, verified_name, business_id, business_name,
               business_account_id, overall, last_checked_at, checks: [...] }],
  recent_alerts: [{ id, severity, summary, occurred_at, business_name }],
  notes: [...] }
check = { code, state: ok|warning|problem|unknown, title, detail, next_step,
          fix_in: console|meta|none, basis: provider|console, action: ""|"refresh" }
```

Rules: a missing signal is `unknown`, never `ok`; a provider value the app does
not recognise is shown verbatim as a warning; `basis: console` marks this app's
own thresholds (stale after 24 h, failure rate over the last 24 h) as distinct
from what Meta reported; `fix_in: meta` means the fix exists only in Meta's own
interface (for example WhatsApp Manager). `last_checked_at` lets a client verify
that a refresh (`POST /accounts/<id>/verify|sync/`) really re-read Meta.

## Platform: backups

`GET /api/v1/platform/backups/` (platform staff): history of database backup
attempts (status, file, size, sha256, encrypted, error, disk space).

## Realtime events

The server sends `{"event", "data", "emitted_at"}`, where `event` ∈
`connection.ready, message.created, message.status_changed,
message.media_ready, conversation.created, conversation.updated,
conversation.assigned, conversation.tags_changed, contact.updated,
number.status_changed, account.alert, campaign.updated, campaign.progress`
(the campaign events carry `{id, business_id, status, progress}` with
persisted counters). Events are published only after the
database transaction commits. After a reconnect the console re-reads its
lists over REST (resync), so no event is lost from the user's view. Payloads carry ids and small summaries. Clients refetch
over REST (re-authorised). The client may send `{"type": "ping"}` → `pong`.
Connect with `/ws/v1/realtime/?organization=<uuid>&business=<uuid>`. Events
are delivered per business, so a socket only receives events of a business it
may access. Close codes: `4401` unauthenticated, `4403` not a member, missing
permission, or no access to the business.

<a id="rate-limits"></a>
## Authentication error codes

| Code | Status | Meaning |
|---|---|---|
| `MFA_REQUIRED` | 401 | the session never completed the second factor; sign in again |
| `INVALID_MFA_CODE` | 401 | wrong, expired or already-used code |
| `MFA_SESSION_EXPIRED` | 401 | the password step expired or was used up; start sign-in again |
| `MFA_LOCKED_OUT` | 429 | too many wrong codes for this account; wait about 15 minutes |
| `REAUTHENTICATION_FAILED` | 403 | wrong current password on a sensitive action |
| `MFA_SETUP_REQUIRED` | 403 | platform access needs MFA enrolled (`MFA_REQUIRED_FOR_STAFF`) |
| `OPERATIONAL_LIMIT_REACHED` | 409 | a per-organization safety cap was reached (`details.limit`) |

## Rate limits

See [messaging-architecture.md](messaging-architecture.md#rate-limiting-summary).
