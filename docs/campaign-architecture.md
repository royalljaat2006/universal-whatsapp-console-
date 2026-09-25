# Campaign architecture (Build Cycle 3)

A campaign sends one **approved template** from one **WhatsApp number** to an
**audience** of the same business's contacts, asynchronously and at a
configured rate. Code: `backend/apps/campaigns/`.

## Entities

| Concept (spec name) | Implementation |
|---|---|
| Campaign | `Campaign`: business, number, template, variable mapping, audience definition, schedule and timezone, rate, lifecycle timestamps, heartbeat, failure reason |
| CampaignAudience | `Audience` (saved, business-scoped segment) plus `Campaign.audience_definition` (the definition this campaign uses, copied when it is saved) |
| CampaignRecipient | `CampaignRecipient`: the **snapshot**, one row per contact (unique campaign+contact), with the resolved variables, address, status, attempts and failure category |
| CampaignMessage | an ordinary `messaging.Message` with `campaign` set. It goes through the same outbound pipeline, idempotency, status webhooks and costing as every other send, so there is no duplicate concept |
| CampaignExecution | the campaign's `launched_at / started_at / paused_at / completed_at / cancelled_at / last_tick_at` plus its events |
| CampaignEvent | `CampaignEvent`, an append-only lifecycle log (created, updated, scheduled, launched, snapshot_built, started, paused, resumed, cancelled, failed, retry_requested, completed) |

## Lifecycle

```
draft ──launch(confirm)──► scheduled ──(beat: due)──► queued ──► running ──► completed
   │                           │                        │          │  ▲
   └── delete                  └── cancel               pause ◄────┘  └── retry-failed (safe only)
                                                        resume ─────►
running ──(number/template/credential stops working)──► failed ──resume (re-checked)──► running
scheduled | queued | running | paused | failed ──cancel──► cancelled
```

- **Launch** is an explicit action (`{"confirm": true}`) after the launch
  summary (`GET …/preview/`) has shown every check. Blocking checks cover
  ownership, number connected, WABA connected, provider credentials, template
  approved and supported, variables mapped, audience valid, non-empty and
  within `CAMPAIGN_MAX_RECIPIENTS`, rate limit, and a future schedule.
  Warnings cover recipients missing a variable (they will be skipped),
  recipients without a recorded opt-in, and the audience exceeding the
  number's messaging-limit tier.
- A scheduled campaign is **re-checked when it starts**, because templates or
  connections can change in between.

## Execution

```
launch (HTTP)         validate → SCHEDULED | QUEUED → on commit: start task
beat: every minute    start_due_campaigns → QUEUED → start task
start_campaign        re-check → audience snapshot (one DB query iterated in
                      chunks, 1000-row bulk inserts, variables resolved per
                      contact) → RUNNING → tick
tick (campaigns queue)
  lock campaign (SKIP LOCKED: one tick at a time)
  budget = ceil(rate × CAMPAIGN_TICK_SECONDS / 60); in-flight window = 2 × budget
  claim ≤ budget PENDING recipients (FOR UPDATE SKIP LOCKED) → QUEUED
  for each: enqueue_outbound_message(template, variables,
            idempotency_key = "campaign:<campaign>:<recipient>:<attempt>",
            campaign = …, dispatch_queue = "campaigns")
  nothing pending or in flight → COMPLETED; else schedule the next tick
beat: every minute    watchdog re-kicks RUNNING campaigns whose tick chain died
                      and QUEUED campaigns whose start task was lost
```

- Campaign dispatches run on the isolated **`campaigns`** queue, so a large
  campaign never delays agent replies (`outbound` queue).
- Rate limiting is layered: the campaign rate (ticks), the per-number token
  bucket (existing throttle), and provider feedback (429/130429/131056 are
  retried with backoff by the messaging pipeline).
- If the number, template or credentials stop working mid-run, the tick fails
  the campaign (`SEND_BLOCKED`) and releases any claimed-but-unsent
  recipients back to `pending`. `resume` re-runs the checks.

## Idempotency and retries

| Failure | Why nobody gets the message twice |
|---|---|
| Celery redelivers a tick | campaign row lock with SKIP LOCKED, and recipients are claimed under SKIP LOCKED |
| Worker crash after claiming | orphaned `queued` recipients without a message are re-sent with the **same** idempotency key after 60 s (or immediately on resume) |
| Crash after the message insert | the message and the recipient link are written in one transaction, and dispatch is enqueued only on commit |
| Duplicate / replayed request | `Message(organization, idempotency_key)` is unique, so a replay returns the existing message |
| Provider timeout (outcome unknown) | never retried automatically (`PROVIDER_OUTCOME_UNKNOWN`), and not offered for operator retry |
| Operator "retry failed" | only categories `rate_limit`, `authentication` and `temporary_provider_error` (the provider certainly did not send). It increments `attempts`, which creates a new key. Late webhooks for the old attempt are ignored |

## Status and counters

A recipient's status **mirrors its latest message** (`apps/campaigns/progress.py`,
called from the messaging hook in the same transaction):

`pending → queued → sending → accepted → sent → delivered → read`, `failed`,
plus `skipped` (variable missing or invalid) and `cancelled`.

Campaign counters are a `GROUP BY status` over the recipients (index
`campaign_recipient_status_idx`). Nothing is incremented ahead of the real
event, and "437 / 500 processed" means 437 recipients are in a terminal
dispatch state.

## Realtime

`campaign.updated` (lifecycle change) and `campaign.progress` (persisted
counters) are published to the campaign's business channel after commit.
Progress is coalesced to at most one event every 2 s per campaign, and every
tick publishes one, so the latest state always arrives. The console also
re-reads over REST on reconnect and every 15 s while a campaign is active.

## Variables

See `apps/campaigns/variables.py`. Sources are contact name, display or
profile name, phone number, a contact attribute, or fixed text, with an
optional fallback. The mapping is validated against the template's real
parameter structure. Per recipient, values are resolved at snapshot time and
validated again by the template builder. An empty value with no fallback
**skips** the recipient (`VARIABLE_MISSING`); it is never sent empty.

## Audience

See `apps/campaigns/audience.py`. The whitelisted conditions are: tag,
attribute, phone, name, last activity or last inbound message, conversation
status, opt-in, template received, and created date. They combine with `all`
or `any`. Three rules always apply: the campaign's business only, opted-out
contacts excluded, and contacts with no phone or BSUID excluded. The preview
returns an exact `COUNT` and a 10-contact sample. The browser never receives
the whole audience.

## Reports and exports

`GET …/report/` returns progress, cost (from `MessageCost` of the campaign's
messages, per currency, with the pending-rate count), timing, and failures
grouped by category. CSV exports (`campaign_recipients`, `campaign_summary`)
run asynchronously ([api.md](api.md#exports)).
