# Meta webhooks — verified contract

**Verified:** 2026-09-23 (Meta Webhooks "Getting started" and the WhatsApp
webhook references).

## Endpoint

`GET|POST /webhooks/v1/meta/whatsapp/` (Callback URL in App Dashboard →
WhatsApp → Configuration; HTTPS with a valid certificate is required by Meta).

## Verification handshake (GET)

Query: `hub.mode=subscribe`, `hub.verify_token=<META_WEBHOOK_VERIFY_TOKEN>`,
`hub.challenge=<int>`. Respond `200 text/plain` with the challenge echoed.
Anything else → `403`. The token comparison is constant-time.

## Delivery authentication (POST)

- Header `X-Hub-Signature-256: sha256=<hex>` = HMAC-SHA256 of the **raw body
  bytes** keyed with the **App Secret** (`META_APP_SECRET`).
- Invalid or missing signature → `401`, and the body is **not stored**.

## Delivery semantics

- Failed deliveries are retried immediately and then with decreasing
  frequency for up to **36 hours**. Duplicates must be tolerated.
- Deliveries may be batched (up to 1000 updates), but batching is not
  guaranteed.
- One POST can include several `entry[]` objects (several WABAs, and so
  possibly several tenants).

## Payload shape

```json
{"object": "whatsapp_business_account",
 "entry": [{"id": "<WABA_ID>", "time": 1751247548,
            "changes": [{"field": "messages", "value": { … }}]}]}
```

`field == "messages"` → `value.metadata.{display_phone_number, phone_number_id}`,
`value.contacts[]` (`profile.name`, `wa_id`, `user_id`), and either
`value.messages[]` (inbound: `from`, `from_user_id`, `id`, `timestamp` (unix
seconds, string), `type`, `<type>` object, optional `context`) or
`value.statuses[]` (`id`, `status` ∈ `sent|delivered|read|failed|played`,
`timestamp`, `recipient_id`, `recipient_user_id`, optional `errors[]`,
`pricing`, `conversation`).

Inbound types seen in documentation: `text, image, video, audio, document,
sticker, location, contacts, interactive, button, reaction, order, system,
unsupported, request_welcome`. Unknown types are stored as `unsupported`,
with the original type kept in `content.provider_type`.

`field == "message_template_status_update"` → `value.{event,
message_template_id, message_template_name, message_template_language,
reason, message_template_category, …}`; event values include `APPROVED,
PENDING, REJECTED, PAUSED, DISABLED, IN_APPEAL, FLAGGED, LOCKED, ARCHIVED,
DELETED, …`.

## Fields to subscribe (App Dashboard → Webhook fields)

`messages`, `message_template_status_update`,
`message_template_quality_update`, `phone_number_quality_update`,
`account_alerts`, `account_update`, `business_capability_update`.

`messages` and `message_template_status_update` update the messaging and
template domains. The account-level fields (`phone_number_quality_update`,
`account_alerts`, `account_update`, `business_capability_update`) are handled
as described in [api-assumptions.md](api-assumptions.md#account-webhooks).
Every other field is stored raw, routed to the tenant, and recorded verbatim
as a `WhatsAppAccountEvent` (Cycle 2). This includes
`message_template_quality_update`, which does **not** yet update
`MessageTemplate.quality_score`; a template re-sync refreshes that value.

## Test fixtures

`backend/tests/fixtures/meta/*.json` follow exactly these shapes. They are
test data only.
