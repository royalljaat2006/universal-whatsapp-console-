# Monitoring

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

## Next step (proposed; needs an ADR before adding technology)

Prometheus metrics via `prometheus-client`: request latency histograms,
Meta call latency, webhook ingest-to-processed lag, queue depth per Celery
queue, send and failure rates per number. Also Grafana dashboards and
Sentry for exceptions. These were deliberately not added in phase 1, to
avoid introducing technology before its operational owner and hosting are
decided.
