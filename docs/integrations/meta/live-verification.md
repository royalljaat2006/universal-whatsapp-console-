# Live Meta verification checklist (tests A–F)

These tests need real Meta resources and **cannot be simulated**. Automated
tests use recorded payload shapes only. Record the date, operator, number and
result for each test. A test counts as passed only when it has been observed
end to end.

## Prerequisites (all required)

| # | Item | How to confirm |
|---|---|---|
| 1 | `META_APP_SECRET` and `META_WEBHOOK_VERIFY_TOKEN` in the running backend | `/health/ready` OK; a wrong-signature POST returns 401 |
| 2 | Public **HTTPS** callback reaching nginx (`https://<host>/webhooks/v1/meta/whatsapp/`) | App Dashboard → Webhooks → *Verify and save* succeeds |
| 3 | System User token with `whatsapp_business_messaging` and `whatsapp_business_management` in `PROVIDER_SECRET_META_SYSTEM_USER_TOKEN` (backend **and** worker) | `connect_meta_waba` reports `connection_state=connected` |
| 4 | Kiosk and EPS WABA ids + business portfolio id | `connect_meta_waba --business kiosk …` / `--business eps …` |
| 5 | Numbers registered for Cloud API (`POST /{PHONE_NUMBER_ID}/register`) and assigned to the right WABA in Business Manager | Numbers page shows them `connected` after sync (never typed in manually) |
| 6 | Webhook fields subscribed: `messages`, `message_template_status_update`, `phone_number_quality_update`, `account_alerts`, `account_update`, `business_capability_update` | App Dashboard |
| 7 | At least one `APPROVED` template (with and without a media header) | Templates page after *Sync from Meta* |
| 8 | A test handset with WhatsApp that is **not** one of the business numbers | — |

## Tests

| Test | Steps | Pass criteria |
|---|---|---|
| **A** inbound | From the handset, send text to a Kiosk number | Webhook event `processed`; the conversation appears live in the Kiosk Inbox with no refresh; it is **not** visible in EPS |
| **B** outbound | Within 24h, reply from the Inbox Text tab | Message goes `queued → accepted`, and Meta returns a `wamid` (`external_id`); the handset receives it |
| **C** status lifecycle | Keep the conversation open while the handset reads B | Ticks move `sent → delivered → read` live; status history lists every webhook; no regression if Meta sends events out of order |
| **D** template | Pick a conversation outside the window (or wait 24h). Composer shows *template required*. Send an approved template with variables | The handset receives the rendered template; the message shows the template name; when delivered, `cost_status` is `pending_rate` (no rate card) or `priced` (rate card loaded) |
| **E** inbound media | Send an image and a PDF from the handset | Attachment `pending → stored`; image renders, PDF downloads; files land under `org/<id>/biz/<id>/…` in storage |
| **F** outbound media | Attach JPEG/PNG (≤ 5 MB) and a PDF from the Attach tab | Upload succeeds (`POST /{PHONE_NUMBER_ID}/media`), the handset receives both, captions appear on the image/PDF |

Also check: a Kiosk-only agent sees none of the EPS conversations from these
tests, and an EPS-only agent sees none of the Kiosk ones.

## If something fails

Use the troubleshooting table in [onboarding-runbook.md](onboarding-runbook.md#5-troubleshooting).
Keep the `request_id` shown in the console error, then search the backend
and worker logs for it.
