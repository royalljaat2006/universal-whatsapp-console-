# WhatsApp Platform

A multi-tenant WhatsApp management and communication platform on the
WhatsApp Cloud API. The design supports many organizations, WABAs and
numbers, and millions of messages, starting with one organization and five
numbers.

**State: Build Cycle 1 complete.** Organization → Business (Kiosk, EPS, …) →
WABA → numbers, the real Meta Cloud API integration (connect/verify/sync,
send text), the webhook pipeline, inbound and outbound messages with status
tracking, business-isolated contacts and conversations, and a minimal realtime
Inbox. Templates, campaigns, analytics and billing UIs are labelled
`NOT IMPLEMENTED`. There is no mock data anywhere.

## Stack

Django 5.2 LTS + DRF · PostgreSQL 17 · Redis 7 · Celery · Django Channels ·
Next.js 16 + TypeScript + Tailwind + shadcn/ui · Docker Compose · nginx.

## Quick start

```bash
cp .env.example .env
docker compose up --build                                   # → http://localhost:8080
docker compose exec backend python manage.py bootstrap_organization \
    --name "Eko" --owner-email you@example.com
docker compose exec backend python manage.py create_business --organization eko --name "Kiosk"
docker compose exec backend python manage.py create_business --organization eko --name "EPS"
```

Connect real WhatsApp numbers: [docs/integrations/meta/onboarding-runbook.md](docs/integrations/meta/onboarding-runbook.md).

## Repository layout

```
backend/            Django modular monolith
  config/           settings (development/test/staging/production), urls, asgi, celery
  core/             shared kernel: ids, context, logging, errors, secrets, rate limit, health
  apps/             identity · organizations · teams · audit · whatsapp · contacts ·
                    conversations · templates · messaging · campaigns · billing ·
                    analytics · notifications · webhooks · realtime
  providers/        provider abstraction; whatsapp/meta/ = all Meta-specific code
  workers/          queue topology, retry policy
  tests/            unit · integration · api (real Postgres, fake Graph API)
frontend/           Next.js: app/ (routes) · features/ · components/ · lib/ · services/ · stores/ · hooks/ · types/
infrastructure/     docker/ (backend, frontend) · nginx/ · monitoring/
docs/               architecture, database, api, security, webhooks, messaging,
                    billing, analytics, deployment, development, adr/, integrations/meta/
```

## Documentation

| | |
|---|---|
| [Architecture](docs/architecture.md) | modules, boundaries, data flows |
| [Database](docs/database.md) | entities, constraints, indexes, tenancy |
| [API](docs/api.md) | conventions, error contract, endpoints, realtime |
| [Security](docs/security.md) | secrets, RBAC, tenant isolation, audit, retention |
| [Webhooks](docs/webhook-architecture.md) | ingestion, idempotency, replay |
| [Messaging](docs/messaging-architecture.md) | state machine, outbound pipeline, rate limits, campaigns |
| [Billing](docs/billing-architecture.md) · [Analytics](docs/analytics-architecture.md) | cost and metrics design |
| [Deployment](docs/deployment.md) · [Development](docs/development.md) | operations |
| [ADRs](docs/adr/README.md) | architecture decisions |
| [Meta integration](docs/integrations/meta/README.md) | verified API assumptions (Graph v26.0) |
