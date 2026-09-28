# Monitoring

## What exists (phase 2)

- **`GET /metrics`** (core.metrics, core.health.views.metrics): Prometheus
  exposition format. Gated by a bearer token (`METRICS_TOKEN`) rather than a
  session — Prometheus has no login — and disabled (404) until that token is
  set, so it is opt-in exactly like the checks below it. Exposes:
  - `http_request_duration_seconds{method,path_template,status}` — a
    histogram, recorded in `core.middleware.AccessLogMiddleware` (the same
    timer that already produces the `http.access` log line, not a second
    timer). `path_template` is the resolved URL *pattern*
    (`request.resolver_match.route`), not the raw path, so a contact id in
    the URL never creates a new time series per contact.
  - `celery_queue_depth{queue}` — `LLEN` on each of the 7 named broker
    queues in `workers/queues.py` (webhooks, outbound, media, campaigns,
    sync, analytics, default).
  - `celery_workers_online` / `dependency_up{dependency}` — the existing
    `core.health.checks` probes (database, Redis cache, Celery ping),
    re-exposed as gauges instead of only a health-endpoint JSON blob.
  - `webhook_events_by_status{status}` / `messages_failed_by_error_code{error_code}`
    — the exact two SQL-based signals this README already listed under
    "Signals to alert on now" below, turned into scrapable gauges.
  - Recomputed fresh on every scrape (typically every 15-60s), not kept
    live in memory — simpler than keeping counters in sync with the
    database from every request/task path, and cheap at that interval.
- **`infrastructure/monitoring/alerts.yml`**: Prometheus alerting rules for
  the metrics above. Configuration only — nothing runs it. Load it into a
  self-hosted Prometheus + Alertmanager once one exists.
- No Prometheus server, Grafana or Sentry is deployed by this repo — an
  operator points their own self-hosted Prometheus at `/metrics` (example
  scrape config below) and loads `alerts.yml` into it. This keeps the
  decision of *where* that runs and *who* owns it exactly where phase 1 left
  it: outside this repo, made by whoever operates the deployment.

```yaml
# prometheus.yml (on the operator's own Prometheus, not part of this repo)
scrape_configs:
  - job_name: whatsapp-platform
    scrape_interval: 30s
    bearer_token: "<METRICS_TOKEN>"
    static_configs:
      - targets: ["<host>:8080"]
    metrics_path: /metrics
rule_files:
  - alerts.yml
```

## What exists (phase 1)

- **Structured JSON logs** (`LOG_FORMAT=json`) from Django, Celery and nginx.
  Every line carries `request_id`, plus `organization_id` and `user_id` when
  known. Secrets are redacted in the formatter.
- **Correlation:** nginx assigns or propagates `X-Request-ID` → Django →
  Celery task headers → worker logs. Raw webhook events store the ingest
  `request_id`, and processing re-binds it.
- **Latency fields in logs:** `http.access` (`duration_ms` per request),
  `providers.meta` (`duration_ms`, `http_status`, `graph_path` per Graph call),
  `webhook accepted` (`ingest_ms`), and `WebhookProcessingAttempt.duration_ms`
  in the database.
- **Health:** `/health/live`, `/health/ready`, `/health/dependencies`
  (staff only: Celery worker ping and Meta configuration).

## Signals to alert on now (log- or SQL-based)

| Signal | Source |
|---|---|
| Webhook processing failures | log `webhook processing failed`; `WebhookEvent.status IN ('failed','dead')` |
| Dead webhook events | `status='dead'` count > 0 |
| Rejected webhook signatures (spike) | log `webhook signature rejected` |
| Send failures by code | `messaging_message` where `status='failed'`, grouped by `error_code` |
| Outcome-unknown sends | `error_code='PROVIDER_OUTCOME_UNKNOWN'` (needs human review) |
| Provider auth failures | `error_code='PROVIDER_AUTH_FAILED'` (token expired or revoked) |
| Readiness | `/health/ready` non-200 |

## Next step (still proposed; needs an ADR before adding technology)

Grafana dashboards on top of the `/metrics` series above, and Sentry (or
similar) for exception aggregation. Deliberately not added yet, for the same
reason phase 1 deferred Prometheus itself: introducing a new *service*
(rather than a scrape endpoint an operator's own Prometheus can hit) means
deciding its operational owner and hosting first. Per-Graph-call latency and
webhook ingest-to-processed lag are already in the structured logs above and
could become metrics too, the same way the two SQL-based signals were —
deferred here only because they weren't already documented as alert
signals, not because they're technically harder.
