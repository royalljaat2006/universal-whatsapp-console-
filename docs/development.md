# Local development

## Prerequisites

Docker Desktop, plus (for running tests and tools on the host) `uv` for
Python 3.13 and Node 24.

## Full stack

```bash
cp .env.example .env            # dev placeholders only
docker compose up --build       # http://localhost:8080
docker compose exec backend python manage.py bootstrap_organization \
    --name "Dev Org" --owner-email you@example.com
docker compose exec backend python manage.py create_business --organization dev-org --name "Kiosk"
docker compose exec backend python manage.py create_business --organization dev-org --name "EPS"
```

Services: `postgres`, `redis`, `backend` (uvicorn --reload, auto-migrates in
dev), `worker` (all queues), `beat`, `frontend` (next dev), `nginx`
(`127.0.0.1:8080`). Postgres and Redis are published on 127.0.0.1 for host
tools.

With no WABA connected, the console honestly shows **NOT CONNECTED**. To
connect a real one, follow
[integrations/meta/onboarding-runbook.md](integrations/meta/onboarding-runbook.md).
To receive webhooks locally you need a public HTTPS tunnel to port 8080 (for
example ngrok). Meta cannot reach localhost.

## Backend (host)

```bash
docker compose up -d postgres redis
cd backend
uv sync
uv run pytest                    # 199 tests; real PostgreSQL, fake Graph API
uv run ruff check . && uv run black --check . && uv run mypy .
DJANGO_SETTINGS_MODULE=config.settings.test uv run python manage.py makemigrations --check
```

Tests never call Meta: an autouse fixture installs a fake Graph transport
(`tests/fakes.py`) and any unrouted call fails the test.

## Frontend (host)

```bash
cd frontend
npm install
npm run dev          # proxies /api and /health to BACKEND_INTERNAL_URL (default http://localhost:8000)
npm run lint && npm run typecheck && npm run format:check && npm run build
npm test             # Vitest
```

> Frontend tests run from the normal checkout (spaces in the path are fine).
> An earlier "worker failed to start" error was a cold-start timeout (jsdom
> loaded by several workers at once while the OS scanned a fresh
> `node_modules`). The Vitest config now uses the `forks` pool with at most
> 2 workers. If it ever recurs right after `npm install`, simply re-run.

## Useful commands

| Command | Purpose |
|---|---|
| `manage.py bootstrap_organization` | first org + owner (password from `BOOTSTRAP_OWNER_PASSWORD` or a prompt) |
| `manage.py connect_meta_waba` | connect a WABA (real Graph calls) |
| `manage.py replay_webhook_events --status dead` | replay stored webhooks |
| `/admin/` | operator views of webhook events (with a replay action), numbers, audit |
