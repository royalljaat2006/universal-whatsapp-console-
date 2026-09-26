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

## Rollback

Prefer rolling back **application code only** — redeploy the previous image
tag for web/worker/beat, leave the database schema as-is. This works
whenever the new release's migrations were additive (new nullable column,
new table) — true for the overwhelming majority of migrations in this
project (check `git log -p -- backend/*/migrations/` for the release being
rolled back if unsure; a migration that drops a column or table, or backfills
data destructively, is the one case this doesn't cover). The previous code
version does not query columns/tables it doesn't know about, so it runs
fine against a schema one release ahead of it.

1. Redeploy the prior image tag for `backend` (web + worker + beat use the
   same image).
2. Confirm `/health/ready` returns 200 (not just that containers are
   "running" — readiness fails closed on migration mismatches) and re-run
   the release procedure's smoke test (login, one webhook handshake).
3. Only if the rolled-back code cannot tolerate the newer schema (rare —
   e.g. a migration that renamed or dropped something the old code still
   reads): reverse the specific migration as its own step, same as forward
   migration —
   `docker run … backend python manage.py migrate <app> <previous_migration_name> --noinput`
   — **verified as reversible for that specific migration first**
   (`python manage.py sqlmigrate <app> <migration_name>` to read what
   `migrate` would actually run, both directions, before trusting it against
   real data), then redeploy code. Never do this against production without
   a fresh backup taken immediately before (see Backups above).
4. If a bad release already sent real WhatsApp messages, wrote billing
   records, or processed webhooks: rolling back code does not undo those
   side effects — assess what happened via the audit log
   (`GET /api/v1/audit-logs/`) and `WhatsAppAccountEvent`/`MessageCost`
   records before deciding whether further action (customer communication,
   a manual billing correction) is needed. Rollback here means "stop the
   bad code from running," not "undo what it already did."

## Probes

- Liveness: `GET /health/live` (no dependencies).
- Readiness: `GET /health/ready` (DB, pending migrations, Redis).
- `GET /health/dependencies` (staff): adds Celery worker ping and Meta
  configuration presence.
- Container-level (`docker-compose.yml`): `worker` healthchecks via
  `celery inspect ping` against its own instance; `beat` (which doesn't
  answer control pings — it's a scheduler, not a worker) via a `/proc` scan
  for its own process, since this image has neither `ps` nor `pgrep`
  installed. Both feed Docker's own restart/orchestration decisions,
  independent of the HTTP-level checks above.

## Backups

**If PostgreSQL is managed** (the production topology above assumes this —
"PostgreSQL (managed, PITR backups)"), use the provider's own point-in-time
recovery instead of the mechanism below; don't run both against the same
database.

**Self-hosted PostgreSQL** (docker-compose, including local development):
the `backup` service (`docker-compose.yml`) runs `pg_dump` on a schedule
(`BACKUP_INTERVAL_SECONDS`, default 86400 = daily) into the `postgres-backups`
volume, gzip-compressed and timestamped
(`<db>-<UTC timestamp>.sql.gz`), pruning anything older than
`BACKUP_RETENTION_DAYS` (default 7) after every run. It runs once immediately
on startup, then on the configured interval — so restarting the stack always
produces a fresh backup, not just a wait for the next scheduled tick. Logs
each attempt (success with file size, or failure without touching prior
backups) to its own container's stdout (`docker compose logs backup`).

### Restore procedure

1. Identify the dump to restore: `docker compose exec backup ls -la /backups`.
2. **Never restore directly into the live database first.** Create a scratch
   database on the same instance and restore into that:
   ```
   docker compose exec -e PGPASSWORD=<POSTGRES_PASSWORD> postgres \
     psql -U <POSTGRES_USER> -d postgres -c "CREATE DATABASE restore_check;"
   docker compose exec -e PGPASSWORD=<POSTGRES_PASSWORD> backup sh -c \
     "gunzip -c /backups/<file>.sql.gz | psql -h postgres -U <POSTGRES_USER> -d restore_check -q"
   ```
3. Verify integrity before trusting it — compare row counts (and ideally a
   content checksum) against the live database for a few core tables:
   ```
   psql -U <POSTGRES_USER> -d <db> -t -c \
     "SELECT 'identity_user', count(*) FROM identity_user;"
   ```
   repeated against `restore_check`; counts (and a
   `md5(string_agg(...))`-style checksum of a real column) must match.
4. Drop the scratch database once satisfied:
   `DROP DATABASE restore_check;`.
5. Only once step 3 has passed for the specific backup you intend to use,
   restore into a real target: stop application traffic (readiness check
   returning 503 is fine — do not stop before then), then
   `gunzip -c <file>.sql.gz | psql -h <host> -U <user> -d <target-db>`,
   confirm with `manage.py check` and `/health/ready`, then resume traffic.

**Verified 2026-09-26** (local development stack, not production): ran the
full cycle above — a real `pg_dump` produced by the `backup` service,
restored into a scratch database, row counts for `identity_user`,
`organizations_organization`, `contacts_contact` and `audit_auditlog` matched
exactly, and an md5 checksum of every user's email (sorted) was
byte-identical between the live and restored databases. This proves the
mechanism works; it is not a substitute for periodically re-running the same
restore test against production's actual backup source (managed PITR or
this same `pg_dump` mechanism, whichever is in use there).

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
- Database backups verified by a restore test (see Backups above — done
  against the local dev stack 2026-09-26; re-verify against whatever backup
  source production actually uses before go-live).
- Error alerting on `webhook processing failed`, DEAD webhook events and
  failed sends (see `infrastructure/monitoring/README.md`).
- `EMAIL_BACKEND`/`EMAIL_HOST*` point at a real SMTP provider — the default
  console backend only logs, nothing is actually delivered (see .env.example
  for Gmail App Password setup as a temporary option).
- `META_APP_ID`, `META_APP_SECRET` and `META_EMBEDDED_SIGNUP_CONFIG_ID` are
  set if customers should be able to connect their own WhatsApp Business
  Account through Embedded Signup — without these the "Connect WhatsApp"
  button correctly reports "not configured yet" rather than failing
  silently, but the feature doesn't work.
- `NEXT_PUBLIC_SUPPORT_EMAIL` set if the Help & Support panel's "Contact
  support" link should work, rather than show "not configured yet".
- The app-shell CSP (`frontend/proxy.ts`) was verified against this exact
  Meta SDK integration in local development (Playwright,
  `e2e/csp.spec.ts` + `e2e/embedded-signup-csp.spec.ts`) — re-run
  `npm run test:e2e` against the production domain once deployed, since a
  CSP is enforced per-origin and this is the one piece of this checklist
  that a passing local test doesn't fully guarantee for a different host.
