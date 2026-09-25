# Monitoring and alerting

The pre-existing baseline is `infrastructure/monitoring/README.md`:
structured JSON logs correlated end-to-end (nginx → Django → Celery) via
`request_id`/`organization_id`/`user_id`, with a documented table of signals
to alert on today (webhook failures, dead events, send failures by error
code, provider auth failures). It is explicitly log/SQL-based — Prometheus,
Grafana and Sentry are named there as a proposed next step pending an ADR,
not implemented. This cycle did not change that baseline or add a metrics
pipeline; no credentials for an alerting channel (PagerDuty, Slack webhook,
etc.) exist, so no real alert delivery was wired up, per the brief's
instruction not to fabricate monitoring.

## What Cycle 4 added: one read endpoint, no new instrumentation

`GET /api/v1/platform/operations/` (staff-only, `apps/platform/api.py`)
surfaces a snapshot of exactly the signals `infrastructure/monitoring/README.md`
already says to watch, in one place instead of a SQL query:

```json
{
  "organizations": {"total": 12, "active": 11, "suspended": 1},
  "webhooks_last_24h": {"dead": 0, "failed": 2, "processed": 4310, "partial": 1},
  "infrastructure": {
    "database": {"ok": true, "duration_ms": 1.2, "critical": true},
    "cache": {"ok": true, "duration_ms": 0.4, "critical": true},
    "celery_workers": {"ok": true, "duration_ms": 8.1, "critical": false}
  }
}
```

It reuses `core.health.checks` (the same functions `/health/dependencies`
already calls) rather than duplicating health-check logic, and reads
`WebhookEvent` directly rather than adding a new aggregation table. This is a
dashboard data source, not an alerting system — nothing pages anyone yet.

## What's still needed for real alerting

Exactly what `infrastructure/monitoring/README.md` already scopes as the next
step: pick a metrics/alerting stack (that doc names Prometheus + Grafana +
Sentry as candidates), get the relevant credentials (a Sentry DSN, an
Alertmanager/Slack/PagerDuty webhook), and wire the existing structured logs
and the new `/platform/operations/` counts into it. Not started in this
cycle — no such credentials exist yet.
