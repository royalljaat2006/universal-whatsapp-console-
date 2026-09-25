# Analytics architecture (Build Cycle 3)

```
messages, status timestamps, MessageCost   ← source of truth (never deleted by analytics)
   │  every create/status/timestamp/cost change (same transaction)
   ▼
AnalyticsDirtyBucket (business, UTC date)   INSERT … ON CONFLICT DO NOTHING
   │  beat, every minute (analytics queue)
   ▼
recompute_business_day(business, local date)   advisory lock, delete + insert
   ├─ MessageMetricDaily    (business, date, number, template?, campaign?)
   ├─ MessageMetricHourly   (business, local hour, …)
   ├─ CostMetricDaily       (… , category, currency)  ← sums of MessageCost
   └─ ContactActivityDaily  (business, date, number, contact, template?, campaign?)
   ▼
/api/v1/analytics/*   read rollups only, filtered by the caller's businesses
```

## Principles

- **Derived data.** Rollups can be dropped and rebuilt from source at any time:
  `python manage.py rebuild_analytics --from YYYY-MM-DD --to YYYY-MM-DD [--business <id>]`.
  Run it once after deploying Cycle 3 to aggregate earlier history.
- **Recompute, never increment.** Each (business, day) is replaced atomically,
  so late webhooks, replays and retries converge to the same numbers.
- **Cohort bucketing.** A message is counted in the bucket of its
  `occurred_at` (enqueue time for outbound, provider time for inbound). Its
  delivered/read/failed outcome is added to that same bucket when it arrives,
  so a day's delivery rate compares like with like.
- **Business-local days.** Daily buckets are calendar days in
  `Business.timezone` (else `Organization.timezone`, else UTC). Hourly buckets
  are local hours. This also works for non-whole-hour offsets such as
  Asia/Kolkata (+05:30).
- **Dashboards never scan messages.** The one exception is the failures view,
  which reads only *failed* messages of the selected businesses and days.
- **Safety net.** Hourly `recompute_recent_days` recomputes the last two local
  days of every business in case a dirty mark was lost.

## Counters

`inbound, outbound, in_flight (queued/sending), accepted, sent, delivered,
read, failed, rejected (failed before WhatsApp accepted), templates_sent,
media_sent, media_received, cost_priced, cost_pending_rate, cost_free`.
"Delivered" counts messages that WhatsApp reported delivered *or* read (read
implies delivery), using the status or its timestamp, so out-of-order
webhooks still count.

Distinct counts cannot be summed from daily counters, so they come from
`ContactActivityDaily`:

- **unique contacts** = distinct contacts in range;
- **active conversations** = distinct (number, contact) pairs;
- **template unique recipients** = distinct contacts with outbound on that template.

## Rates and "no data"

`delivery_rate = delivered / (outbound − in_flight)`,
`failure_rate = failed / (outbound − in_flight)`,
`read_rate = read / delivered`,
`inbound_outbound_ratio = inbound / outbound`. When the denominator is 0 the
API returns `null`, and the console shows "No data available" (or "—"),
never a fabricated 0 %.

## Cost

Costs come only from persisted, immutable `MessageCost` rows (Cycle 2 rate
cards). They are summed per currency; currencies are never converted. The API
reports `state`:

- `none`: no billable messages;
- `pending_rate`: billable messages exist but no rate card covers them;
- `partial`: some billable messages are priced, some are pending;
- `priced`: everything billable is priced.

## Retention

Source messages and events are **not** deleted because rollups exist. No
automatic retention policy is active. If one is introduced, it must be
documented here and must run `rebuild_analytics` *before* any source rows are
removed. Rollup tables are small: one row per business, day, number,
template and campaign that had activity.
