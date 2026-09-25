# Runbook — connecting real WhatsApp Business Accounts

Audience: the platform operator. Meta-side prerequisites (Business Portfolio,
Meta App, WABA, verified and **registered** numbers, System User token,
payment method, business verification) are covered step by step in the
internal *WhatsApp Cloud API Setup Guide*. This runbook starts where that
guide ends.

## 1. Provide secrets to the deployment (never to the repo)

| Env var | Value |
|---|---|
| `META_APP_ID` | App ID (not secret) |
| `META_APP_SECRET` | App Secret, used to verify webhook signatures |
| `META_WEBHOOK_VERIFY_TOKEN` | Random string you choose (for example `python -c "import secrets;print(secrets.token_urlsafe(32))"`) |
| `PROVIDER_SECRET_META_SYSTEM_USER_TOKEN` | The System User token |
| `META_ACCESS_TOKEN_REFERENCE` | `env:PROVIDER_SECRET_META_SYSTEM_USER_TOKEN` |

If the Kiosk and EPS WABAs use different tokens, add another
`PROVIDER_SECRET_*` variable and pass `--credential-ref
env:PROVIDER_SECRET_<NAME>` in step 3. Restart `backend`, `worker` and `beat`
after changing env vars.

## 2. Register the webhook in the App Dashboard

- Callback URL: `https://<public-host>/webhooks/v1/meta/whatsapp` (with or
  without a trailing slash).
- Verify token: the value of `META_WEBHOOK_VERIFY_TOKEN`.
- Subscribe the fields listed in [webhooks.md](webhooks.md) (at least
  `messages` and `message_template_status_update`).

Meta requires public HTTPS with a valid certificate. For local testing use a
tunnel to `http://localhost:8080` (see the setup guide, stage 8C).

## 3. Create the organization and businesses, then connect each WABA

```bash
docker compose exec backend python manage.py bootstrap_organization \
    --name "Eko" --owner-email owner@yourcompany.com        # prompts for password
docker compose exec backend python manage.py create_business --organization eko --name "Kiosk"
docker compose exec backend python manage.py create_business --organization eko --name "EPS"
docker compose exec backend python manage.py connect_meta_waba \
    --organization eko --business kiosk --waba-id <KIOSK_WABA_ID> --portfolio-id <PORTFOLIO_ID>
docker compose exec backend python manage.py connect_meta_waba \
    --organization eko --business eps --waba-id <EPS_WABA_ID> --portfolio-id <PORTFOLIO_ID>
```

`connect_meta_waba` makes real Graph calls: `GET /{WABA}` (connection check),
`GET /{WABA}/subscribed_apps` (and `POST` if the app is not yet subscribed),
then `GET /{WABA}/phone_numbers` (sync). It prints each number with its status
and is idempotent.

The same flow is available over the API for owners and admins:
`POST /api/v1/whatsapp/credentials/` (a reference or a token; tokens are
encrypted at rest and never returned), then `POST /api/v1/whatsapp/accounts/`
(queued; progress appears in `connection_state` and `sync_state`, visible on
the Numbers page).

**Staff access:** members with the *all businesses* scope (owners and admins by
default) see every business. Everyone else sees only the businesses granted to
them. Manage this in the console under **Settings → Business access**
(`members.manage`). You cannot change your own access or an owner's.

**Numbers are never typed in.** They appear only from the real
`GET /{WABA}/phone_numbers` sync. Which WABA a number belongs to (and so
whether it is Kiosk or EPS) is decided in Meta Business Manager, not here.

## 3b. Templates and rate cards

1. Create and approve templates in WhatsApp Manager, then press **Templates →
   Sync from Meta** (or `POST /api/v1/templates/sync/`). Only `APPROVED`
   templates are offered in the Inbox composer.
2. Load the pricing markets and your currency's rate card from Meta's official
   rate-card files ([billing-architecture.md](../../billing-architecture.md#loading-rate-cards-operator)).
   Until you do, billable messages show `pending_rate` and no amount.

## 4. Verify end to end

1. **Inbound:** send a WhatsApp message from a phone to a Kiosk number. Django
   admin → Webhook events shows a `processed` event, and the Inbox (`/inbox`,
   business Kiosk) shows the conversation live.
2. **Outbound:** reply from the Inbox composer within 24h. The ticks move
   through `accepted → sent → delivered → read` as Meta's status webhooks arrive.
   After 24h without a customer message the composer offers only templates.
   The complete live checklist is [live-verification.md](live-verification.md).
3. **Isolation:** log in as a Kiosk-only member. Nothing from EPS is visible,
   and requests with the EPS `X-Business-ID` return 404.

## 5. Troubleshooting

| Symptom | Check |
|---|---|
| Handshake fails in the dashboard | Is `META_WEBHOOK_VERIFY_TOKEN` set in the running backend? Is the URL reachable over HTTPS? |
| `401` on webhook POSTs in the logs | `META_APP_SECRET` belongs to a different app |
| Events are `partial` | That number or WABA is not connected here. Run `connect_meta_waba` |
| `connection_state=error`, code `PROVIDER_AUTH_FAILED` | Token expired, revoked, or missing permissions. Check it in Meta's Access Token Debugger |
| `connection_state=error`, code `CONFIGURATION_REQUIRED` | The credential's env var is missing in the backend/worker containers |
| Sends fail with `SERVICE_WINDOW_CLOSED` | The customer last wrote more than 24h ago (or never wrote). Send an approved template from the composer's Template tab |
| Template tab says no approved templates | Sync templates (Templates page). Check `template_sync_state` / `template_sync_error` on the WABA |
| Inbound media stays "downloading" | Check the `worker` logs for `download_inbound_media`. `CONFIGURATION_REQUIRED` means the credential env var is missing in the worker |
| Costs stay `pending_rate` | No rate card or market covers that recipient yet. Load them (step 3b) |
| Numbers stay `pending` | The number is not registered for Cloud API (`POST /{PHONE_NUMBER_ID}/register`, see the setup guide) |
| `sync_error` mentions "belong to another account" | That number is attached to a different WABA record. Resolve it manually; sync never re-parents numbers |
