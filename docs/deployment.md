# Deployment

## Environments

`APP_ENV` / `DJANGO_SETTINGS_MODULE`: `development` (config.settings.development),
`staging` (config.settings.staging = production hardening), `production`
(config.settings.production, which refuses to start with an insecure secret
key or empty `ALLOWED_HOSTS`). All configuration comes from environment
variables (see `.env.example`). The images contain no secrets.

## Images (one per runtime)

| Image | Dockerfile target | Roles |
|---|---|---|
| backend | `infrastructure/docker/backend/Dockerfile` `production` | web (uvicorn ASGI: HTTP + WebSocket), `celery worker`, `celery beat` via the command. Non-root, static files collected, healthcheck on `/health/live` |
| frontend | `infrastructure/docker/frontend/Dockerfile` `production` | Next.js standalone `node server.js`, non-root |
| nginx | `nginx:1.27-alpine` + `infrastructure/nginx/nginx.conf.template` | TLS termination (add certs in prod), routing, webhook rate limit |

## Production topology (minimum)

```
nginx (443) ─► backend web ×N  (uvicorn --workers 4, stateless)
            └► frontend ×N
celery worker -Q webhooks            (latency-critical, isolated)
celery worker -Q outbound,campaigns  (provider-bound)
celery worker -Q sync,analytics,default
celery beat ×1                       (exactly one)
PostgreSQL (managed, PITR backups)   Redis (AOF on; noeviction)
```

## Release procedure

1. Build and push images (CI).
2. **Migrate as a separate one-off step**:
   `docker run … backend python manage.py migrate --noinput`. Web replicas
   never auto-migrate in production (`RUN_MIGRATIONS` stays unset).
3. Roll web, workers and beat. `/health/ready` returns 503 until migrations
   match the code, so load balancers hold traffic.
4. Smoke test: `/health/ready`, a login, and one webhook handshake.
5. After deploying Cycle 3 for the first time, aggregate pre-existing history
   once: `python manage.py rebuild_analytics --from <first day> --to <today>`
   (idempotent). Beat must run the new jobs `campaigns-start-due`,
   `campaigns-watchdog`, `analytics-aggregate-dirty` and
   `analytics-recompute-recent`. Give the `campaigns` queue its own worker in
   production so bulk sends never compete with agent replies.

## Probes

- Liveness: `GET /health/live` (no dependencies).
- Readiness: `GET /health/ready` (DB, pending migrations, Redis).
- `GET /health/dependencies` (staff): adds Celery worker ping and Meta
  configuration presence.

## Deploying under a path prefix (shared gateway)

When an outer reverse proxy publishes this app at a subpath instead of its
own domain/root (e.g. `https://host/whatsapp-console/` on a box that hosts
several unrelated apps), set `BASE_PATH=/whatsapp-console` (no trailing
slash) wherever the stack is built and run:

- Frontend image: pass `--build-arg BASE_PATH=/whatsapp-console
  --build-arg NEXT_PUBLIC_BASE_PATH=/whatsapp-console` (baked into the
  Next.js build; also set `NEXT_PUBLIC_BASE_PATH` as a runtime env var for
  any server-rendered code path).
- `nginx` service: set `BASE_PATH` in its environment; `nginx.conf.template`
  renders the prefix into its routing at container start via `envsubst`.
- Django: `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` still use
  the bare host — Django itself stays prefix-unaware; this project's own
  nginx strips `BASE_PATH` before proxying to the backend and passes it
  through unchanged to the frontend (which needs it for Next's `basePath`
  routing).
- The **outer** gateway must forward the full incoming path unchanged
  (no prefix-stripping — `proxy_pass http://127.0.0.1:<port>;`, no trailing
  slash) since this project's own nginx expects to see `BASE_PATH` itself.
- Leave `BASE_PATH` unset for a standard root deployment; every path above
  degrades to its current unprefixed form (verified: the template renders
  byte-identical routing when `BASE_PATH=""`).

## Checklist before go-live

- Real HTTPS certificate for the webhook callback. Meta rejects self-signed
  certificates.
- `META_*` and `PROVIDER_SECRET_*` come from the secret store.
- `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` are set to the
  public host.
- Publish only nginx. Postgres and Redis stay on private networks (compose
  binds them to 127.0.0.1 in development).
- Database backups verified by a restore test.
- Error alerting on `webhook processing failed`, DEAD webhook events and
  failed sends (see `infrastructure/monitoring/README.md`).
