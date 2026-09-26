# Meta WhatsApp Cloud API — verified assumptions

**Verified:** 2026-09-23 against official Meta developer documentation
(developers.facebook.com). Re-verify before changing
`META_GRAPH_API_VERSION` or adding any new Meta call. Nothing in the codebase
may depend on Meta behaviour that is not listed here.

**Embedded Signup section re-verified 2026-09-26** (WebFetch against
developers.facebook.com/documentation/business-messaging/whatsapp/
embedded-signup/{overview,implementation}, /access-tokens/, and
/embedded-signup/onboarding-customers-as-a-tech-provider — see that section
below). Embedded Signup **v2 is deprecated 2026-10-15**; this integration
targets v4 (`config_id` + `response_type: 'code'` flow), which is unaffected.

## API version

| Item | Value | Source |
|---|---|---|
| Latest Graph API version | **v26.0** (released 2026-07-29) | Graph API changelog |
| v25.0 available until | 2028-07-29 | changelog |
| v24.0 available until | 2028-02-18 | changelog |
| v23.0 available until | 2027-10-08 | changelog |

- Configured once: `META_GRAPH_API_VERSION` (default `v26.0`), validated as
  `vNN.N` in `providers/whatsapp/meta/config.py`. No version string appears
  anywhere else in the code.
- Base URL: `META_GRAPH_API_BASE_URL` (default `https://graph.facebook.com`).
- The internal setup guide (Aug 2026) uses `v23.0` in examples. That still
  works, but we default to the latest version.

## Authentication

- Bearer token in `Authorization: Bearer <token>`.
- Production uses a **System User** token (non-expiring) with the
  `whatsapp_business_messaging`, `whatsapp_business_management` and
  `business_management` permissions. Temporary 24h tokens are for testing only.
- Tokens are never stored in plain columns. See `docs/security.md`.

## Endpoints used (all relative to `/{version}`)

| Purpose | Method + path | Where |
|---|---|---|
| Send message | `POST /{PHONE_NUMBER_ID}/messages` | `MetaWhatsAppProvider.send_message` |
| Mark read | `POST /{PHONE_NUMBER_ID}/messages` body `{"messaging_product":"whatsapp","status":"read","message_id":…}` | `mark_message_read` |
| List numbers | `GET /{WABA_ID}/phone_numbers?fields=…` (cursor paging) | `list_phone_numbers` |
| Get number | `GET /{PHONE_NUMBER_ID}?fields=…` | `get_phone_number`, `health_check` |
| WABA info (connection check) | `GET /{WABA_ID}?fields=id,name,timezone_id,message_template_namespace,account_review_status,business_verification_status,country,ownership_type` | `get_business_account` |
| Is our app subscribed? | `GET /{WABA_ID}/subscribed_apps` → `{"data": [...]}` | `is_app_subscribed` (only *non-empty `data`* is relied upon; the entry shape is not documented clearly enough to parse) |
| Business profile | `GET /{PHONE_NUMBER_ID}/whatsapp_business_profile?fields=about,address,description,email,profile_picture_url,websites,vertical` → `{"data":[{…}]}` | `get_business_profile` |
| List templates | `GET /{WABA_ID}/message_templates` | `list_templates` |
| Create template | `POST /{WABA_ID}/message_templates` (`name`, `language`, `category`, `components`, optional `parameter_format`) | `create_template` |
| Edit template | `POST /{TEMPLATE_ID}` | `update_template` |
| Delete template | `DELETE /{WABA_ID}/message_templates?name=…&hsm_id=…` | `delete_template` |
| Subscribe app to WABA webhooks | `POST /{WABA_ID}/subscribed_apps` → `{"success": true}` | `subscribe_webhooks` (also used to finish an Embedded Signup connection — same call, no separate endpoint) |
| Register number | `POST /{PHONE_NUMBER_ID}/register` `{"messaging_product":"whatsapp","pin":"<6 digits>"}` | `register_phone_number`, called once by `complete_embedded_signup` with a freshly generated PIN (not persisted — not needed again; a manual re-registration after that is still an operator runbook item) |
| Embedded Signup code exchange | `GET /oauth/access_token?client_id=&client_secret=&code=` → `{"access_token": "<business token>"}` | `MetaWhatsAppProvider.exchange_code_for_token` — no bearer token on this call, it's what produces one |
| Upload media (Cycle 2) | `POST /{PHONE_NUMBER_ID}/media`, multipart `messaging_product=whatsapp`, `type=<mime>`, `file` → `{"id"}` | `upload_media` |
| Media metadata (Cycle 2) | `GET /{MEDIA_ID}?phone_number_id=<id>` → `{url, mime_type, sha256, file_size, id}` | `get_media` |
| Media download (Cycle 2) | `GET <url>` with the same Bearer token. The URL is valid for **5 minutes**, and a 404 means it expired | `download_media` (https + host allow-list `META_MEDIA_DOWNLOAD_HOSTS`, default `lookaside.fbsbx.com`; streamed with a size cap) |

WABA fields verified 2026-09-23 (Cycle 1): `id, name, timezone_id,
message_template_namespace, account_review_status, business_verification_status,
country, ownership_type, primary_business_location`. `currency` is **not** a
documented field, so it is never requested.

Phone-number fields requested (documented as queryable): `id,
display_phone_number, verified_name, status, quality_rating,
code_verification_status, name_status, messaging_limit_tier, throughput`.
Graph rejects the whole call when an unknown field is requested, so only these
are listed.

<a id="embedded-signup"></a>
## Embedded Signup (verified 2026-09-26, v4)

Client-side (JS SDK), server-side exchange, then the same connection
pipeline every WABA goes through (`apps.whatsapp.services.
complete_embedded_signup` -> `register_business_account` ->
`run_connection_pipeline`).

1. Load `https://connect.facebook.net/en_US/sdk.js`, then
   `FB.init({appId, version: 'v25.0', xfbml: true})`. (`appId` /
   `META_APP_ID` is not secret; it is the same value Meta's own client-side
   examples embed directly.)
2. Launch: `FB.login(callback, {config_id: META_EMBEDDED_SIGNUP_CONFIG_ID,
   response_type: 'code', override_default_response_type: true, extras:
   {setup: {}}})`. `config_id` comes from App Dashboard > Facebook Login for
   Business > Configurations — also not secret, but served from
   `GET /api/v1/whatsapp/embedded-signup/config/` so it can be rotated
   without a frontend redeploy.
3. Listen on `window.addEventListener('message', ...)`, checking
   `event.origin.endsWith('facebook.com')`, for a JSON payload with
   `data.type === 'WA_EMBEDDED_SIGNUP'`. On `data.event === 'FINISH'`,
   `data.data` carries `phone_number_id` and `waba_id`. `CANCEL` carries
   `current_step`; treated as a user-abandoned flow, not an error.
4. The `FB.login` callback separately receives
   `response.authResponse.code` — a code valid for **~30 seconds**. The
   frontend must have both the FINISH event's ids and this code before
   calling our backend.
5. `POST /api/v1/whatsapp/embedded-signup/complete/`
   `{business_id, waba_id, phone_number_id, code}` (`numbers.manage`).
   Backend: exchange the code (see table above) for a business token,
   store it as an encrypted `ProviderCredential`
   (`create_encrypted_credential`), `register_business_account`, best-effort
   `register_phone_number`, then queue the standard verify/subscribe/sync
   pipeline exactly like the manual connect flow.

Not automated by this integration (unchanged from the manual flow):
`debug_token` / `granular_scopes` inspection, and instructing the customer to
add a payment method in WhatsApp Manager (Meta-hosted, outside our app).

<a id="send-message"></a>
### Send request/response

Request: `messaging_product: "whatsapp"`, `recipient_type: "individual"`,
`to` (digits with country code, no `+`) **or** `recipient` (BSUID), `type`,
and the type object (`text`, `template`, `image`, …). Optional
`context.message_id` for replies.

Response: `contacts[].{input, wa_id}` and `messages[].{id, message_status}`,
where `message_status` ∈ `accepted | held_for_quality_assessment | paused`.
A 2xx response means *accepted*, **not** sent. Delivery arrives via webhooks.
This is why the internal state machine has an `ACCEPTED` state.

<a id="bsuid"></a>
## Business-scoped user IDs (BSUID)

- Since April 2026, webhooks carry a BSUID for every user: `contacts[].user_id`,
  `messages[].from_user_id`, `statuses[].recipient_user_id` (plus
  `parent_user_id` variants when parent BSUIDs are enabled).
- Format: ISO country code + `.` + up to 128 alphanumerics (`US.1349…`), or
  `US.ENT.…` for parent BSUIDs.
- Scope: unique per **business portfolio**/user pair; regenerated if the user
  changes phone number.
- `wa_id` / `from` / `recipient_id` (phone) **may be omitted** for users who
  enabled usernames. The contact model therefore stores identities
  separately (`ContactIdentity`) and never assumes a phone number exists.
- Sending to a BSUID uses `"recipient": "<BSUID>"`; with both set, the phone
  number takes precedence (we send exactly one).

<a id="media"></a>
## Media (verified 2026-09-23, Cycle 2)

| Kind | Types | Max size |
|---|---|---|
| audio | aac, amr, mp3 (`audio/mpeg`), m4a (`audio/mp4`), ogg (OPUS only) | 16 MB |
| document | txt, pdf, doc(x), xls(x), ppt(x) | 100 MB |
| image | jpeg, png | 5 MB |
| sticker | webp | 500 KB animated / 100 KB static |
| video | mp4, 3gp (H.264 + AAC) | 16 MB |

- An uploaded media id lives **30 days**. Media ids received in webhooks live
  **7 days**. We cache the uploaded id per `MessageAttachment` and re-upload
  after 29 days (`provider_media_uploaded_at`).
- Captions are allowed on image, video and document only. Audio and sticker
  captions are rejected by our backend before any call.
- The checksum returned by `GET /{MEDIA_ID}` is logged when it differs from
  the stored bytes. It is not enforced, because Meta does not document how
  it is computed for every type.
- `apps/media/rules.py` holds this table plus file-signature checks. Uploads
  are validated server-side (type, extension, size, magic bytes) before they
  are stored or sent.

<a id="service-window"></a>
## Customer service window (verified 2026-09-23)

- A 24-hour window opens when the user messages (or calls) the business, and
  every new user message or call restarts it.
- Inside the window any message type may be sent. Outside it (or before the
  user has ever written) **only approved templates** may be sent.
- Enforced server-side in `apps/messaging/policy.py`, which returns
  `SERVICE_WINDOW_CLOSED` with `details.policy`. The Inbox shows the same
  policy, but never decides it.

<a id="templates"></a>
## Template sending (verified 2026-09-23)

- `type: "template"`, `template: {name, language: {code}, components[]}`.
- Parameters: positional (`{{1}}`) or named (`parameter_format: NAMED`, with
  each parameter carrying `parameter_name`).
- A TEXT header allows **at most one** variable. A media header is
  `{"type": "image"|"video"|"document", "<kind>": {"id"}}`. We always send an
  uploaded media id, never a public link.
- URL buttons with a variable suffix: `{"type": "button", "sub_type": "url",
  "index": "<n>", "parameters": [{"type": "text", "text": …}]}`.
- The console builds components server-side from `{header, body, buttons}`
  values (`apps/templates/parameters.py`). It rejects missing, empty or
  unknown values with `TEMPLATE_PARAMETERS_INVALID` and `details.fields`.
  CAROUSEL and LIMITED_TIME_OFFER templates are reported as not sendable from
  the console.

<a id="account-webhooks"></a>
## Account-level webhook fields (verified 2026-09-23)

| Field | Payload (value) | Our handling (`apps/whatsapp/account_events.py`) |
|---|---|---|
| `phone_number_quality_update` | `display_phone_number, event, current_limit, old_limit?, max_daily_conversations_per_business?`. **No phone number id**, so the number is matched by display digits within the WABA | updates `messaging_limit_tier` (tiers `TIER_50 … TIER_UNLIMITED`) and emits `number.status_changed` |
| `account_alerts` | `entity_type, entity_id, alert_info{alert_severity CRITICAL/WARNING/INFORMATIONAL, alert_status, alert_type, alert_description}` | severity mapped; a `PHONE_NUMBER` entity is linked by external id; warning/critical emit `account.alert` |
| `account_update` | `event` (e.g. `ACCOUNT_RESTRICTION`, `ACCOUNT_VIOLATION`, `ACCOUNT_DELETED`, `DISABLED_UPDATE`, `PARTNER_REMOVED`, `ACCOUNT_OFFBOARDED`, `ACCOUNT_RECONNECTED`, …) + `*_info` objects | restriction → WABA `restricted`; deleted/offboarded/partner removed → connection `disconnected`; reconnected → `pending`; every event is recorded |
| `business_capability_update` | `max_daily_conversations_per_business`, `max_phone_numbers_per_waba` | stored in `provider_metadata.capabilities` |

Every event is stored once in `WhatsAppAccountEvent`. It is deduplicated by a
hash of the account, field, value and time, and listed at
`GET /api/v1/whatsapp/events/`.

## Rate limits and throughput

| Limit | Value | Our handling |
|---|---|---|
| Throughput per number | 80 msg/s default (upgradable) | Redis token bucket per number (`throughput_mps`, default `WHATSAPP_DEFAULT_THROUGHPUT_MPS=80`) |
| Pair rate limit | ~1 msg / 6 s to the same user (burst allowed); error **131056** | classified as SAFE retry with backoff |
| Graph API per WABA | 200 req/h default, 5000 req/h for active WABAs | sync jobs run on the `sync` queue; not polled |
| Template creation | 100 per WABA per hour | not automated yet |
| Messaging limits (unique users / 24h) | tiered (e.g. `TIER_250`, `TIER_1K` … `TIER_UNLIMITED`), **shared across the business portfolio** | stored per number (`messaging_limit_tier`); enforcement is the campaign phase |

<a id="error-codes"></a>
## Error codes → retry policy (`providers/whatsapp/meta/errors.py`)

| Codes | Meaning | Classification |
|---|---|---|
| HTTP 429, 4, 80007, 130429, 131056, 133016 | throttling (not executed) | `SAFE` (retry with backoff) |
| 0, 3, 10, 190, 200–299, HTTP 401/403 | auth / permission | `PERMANENT` (`ProviderAuthError`) |
| 131000, 131016 | documented transient failures | `TRANSIENT` (retry) |
| other 5xx | unknown | `TRANSIENT` for GET, **`AMBIGUOUS` for POST** (never auto-retried) |
| everything else (100, 131026, 131047, 131049, 132xxx, 368, …) | request/policy/recipient | `PERMANENT` |
| connect errors / connect timeout | request never sent | `SAFE` |
| read timeout / protocol error after sending | outcome unknown | `AMBIGUOUS` for POST |

## Pricing (see `docs/billing-architecture.md`)

- Per-message pricing since **2025-07-01**. Charged only when a template message
  is **delivered**.
- Status webhooks carry `pricing{billable, pricing_model ("PMP"; "CBP" legacy),
  type ("regular" | "free_customer_service" | "free_entry_point"), category}`
  where category ∈ `authentication, authentication-international, marketing,
  marketing_lite, referral_conversion, service, utility`.
- From v24.0 the `conversation` object is omitted except inside free entry
  point windows. We store it when present and never depend on it.

- Only **delivered template** messages are charged. Non-template messages
  and utility templates sent inside an open customer service window are free.
- The rate depends on the recipient's country calling code, mapped to a
  rate-card **market**, and on the category. Utility and authentication have
  monthly volume tiers per business portfolio. Rates are published per
  currency.

### Conflict with the internal guide (resolved 2026-09-23)

The internal *WhatsApp Cloud API Setup Guide* (Aug 2026) says that from
**2026-10-01** utility templates and service messages inside the 24h window
become chargeable. Meta's official pricing documentation, re-read on
2026-09-23, describes the 1 Oct 2026 update as a **market change only**:
Bangladesh, Iraq, Nepal, Sri Lanka, Kazakhstan, Kuwait, Morocco, Oman and
Ukraine move out of their "Rest of …" regions into their own markets. In-window
utility and service messages **remain free**. We follow the official
documentation.

**Design impact: none.** Billable-ness comes from Meta's `pricing.billable`,
and markets and rates are effective-dated data (`load_pricing_markets`,
`load_rate_card`). Loading the post-October market list is an operator task,
not a code change. See [billing-architecture.md](../../billing-architecture.md).

## Things deliberately NOT assumed

- No webhook event id: dedup uses SHA-256 of the raw body plus domain unique
  keys.
- No ordering guarantee between webhooks (Meta retries for up to 36h).
- No idempotency key on the send API: ambiguous send failures are never
  retried automatically.
- Template status vocabularies differ between the list API and webhook events
  (e.g. `PENDING_REVIEW` vs `PENDING`). Values are stored verbatim, and only
  `APPROVED` is treated as sendable.
