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
the `backup` service (`infrastructure/docker/backup/`) runs `pg_dump
--format=custom` on a schedule (`BACKUP_INTERVAL_SECONDS`, default 86400 =
daily) into the `postgres-backups` volume, named
`<db>-<UTC timestamp>.dump` (or `.dump.enc` when encrypted), each with a
`.sha256` sidecar. It waits for the database (and the `core_backuprecord`
table) to exist, backs up once immediately on startup, then on the
configured interval. A failed attempt is retried
(`BACKUP_RETRY_ATTEMPTS`, default 3, `BACKUP_RETRY_DELAY_SECONDS`, default 60)
before the service waits for the next interval; this covers the common case
of the database still starting after a host or Docker restart. `pg_dump` uses
its own consistent MVCC snapshot and never blocks application writes.

**What gets recorded.** Every attempt, success or failure, is a row in
`core_backuprecord` (status, timestamps, file, size, sha256, encrypted flag,
the real error text on failure, and free/total space of the backup volume):
`GET /api/v1/platform/backups/` (staff only), or the gauges on `/metrics`:
`backup_last_status` (1/0), `backup_last_success_age_seconds`,
`backup_last_size_bytes`, `backup_disk_free_ratio`. When there is nothing to
report the gauges are explicit: age is `+Inf` if no backup has ever succeeded
(so `BackupMissing` fires) and status / disk ratio are `NaN` (so
`BackupFailed` / `BackupDiskSpaceLow` do not fire on missing data).
`infrastructure/monitoring/alerts.yml` defines `BackupFailed`, `BackupMissing`
and `BackupDiskSpaceLow`; their behaviour is covered by
`infrastructure/monitoring/alerts.test.yml` (`promtool test rules`). The
container also has a Docker healthcheck that turns unhealthy if no backup has
succeeded within 2 intervals + 1 hour.

**Per-backup checks, before a file is accepted:** free space is at least 2x
the previous backup (plaintext and ciphertext coexist briefly); the fresh dump
passes `pg_restore --list`; when encrypting, the ciphertext is decrypted again
and its checksum must equal the dump's checksum. Encrypted output is written
to a temporary name and renamed only when verified, so a crash can never leave
a partial file that retention or restore would treat as a backup. Failed
attempts leave earlier backups untouched.

**Encryption at rest.** Set `BACKUP_ENCRYPTION_KEY` (see `.env.example`) and
backups are encrypted with `openssl enc -aes-256-cbc -pbkdf2`. The plaintext
dump exists on the volume only transiently (mode 600, inside a mode 700
directory) while it is being verified and encrypted, and is removed on every
exit path. Leaving the key unset is supported but logs a warning on every run;
do not do it with real customer data. Store the key in a password manager as
well as `.env`: losing it makes every encrypted backup unrecoverable, by
design. Note that `openssl enc` CBC provides confidentiality but not
authentication; integrity is provided by the sha256 sidecar (accidental
corruption) and `pg_restore --list`, not by a MAC, so keep the volume
write-protected from untrusted parties.

**Permissions.** Backup directory `chmod 700`, backup files `chmod 600`
(`umask 077` for the whole service).

**Retention** (GFS, configurable): every backup from the last
`BACKUP_DAILY_RETENTION_DAYS` (default 7) is kept; beyond that, the newest
backup in each rolling 7-day bucket for `BACKUP_WEEKLY_RETENTION_WEEKS`
(default 4); beyond that, the newest per calendar month for
`BACKUP_MONTHLY_RETENTION_MONTHS` (default 6); older ones are removed together
with their sidecar. The newest backup is never removed, however the settings
are chosen. Non-integer settings stop the service at startup instead of
silently changing what is deleted. To see what a pass would delete without
deleting anything:

```
docker compose exec -e BACKUP_PRUNE_DRY_RUN=true backup /entrypoint.sh prune
```

### Restore procedure

The restore tool is `/restore.sh` inside the `backup` image. It checks the
sha256 sidecar, decrypts into a private temp file (removed on exit), verifies
the archive, and recreates the cluster-level RLS role `whatsapp_platform_app`
if missing (it lives outside any single database dump; restoring without it
leaves the app role with `permission denied` on every tenant table, which was
reproduced with the previous `--no-privileges` approach). Table privileges are
restored from the dump.

A dump can also reference its original *owner* role (for example in
`ALTER DEFAULT PRIVILEGES FOR ROLE <owner>`), which aborts a restore onto a
server where that role does not exist (new cluster, managed Postgres, a
different `POSTGRES_USER`). `restore.sh` therefore creates any role the dump
references as a `NOLOGIN` placeholder, and re-applies the app role's grants and
default privileges for the role that is restoring, so tables created by future
migrations stay reachable. Found by restoring a real backup onto a server with
a different owner name; covered in the verification below.

**Verify a backup, never touching the real database:**
```
docker compose exec backup /restore.sh <file.dump|file.dump.enc>
```
Restores into a throwaway `restore_check_<timestamp>` database, then checks
that migrations are present, RLS policies exist, the app role can query tenant
tables and sees **no rows** without a tenant context (fail-closed), and prints
live-vs-restored row counts (informational, since live data changes after the
backup). Exits non-zero on any `[FAIL]`. The scratch database is dropped.

**Restore into a named database** (requires the target name AND
`CONFIRM_PRODUCTION_RESTORE=yes`):
```
docker compose exec -e CONFIRM_PRODUCTION_RESTORE=yes backup \
  /restore.sh <file.dump.enc> <target-db-name>
```
The backup is restored into a staging database and checked first; then the
databases are swapped by rename. The previous database is **kept** as
`<target>_pre_restore_<timestamp>` (drop it yourself once you are satisfied),
so a failed or mistaken restore never destroys the data it was meant to
replace. Stop application traffic first: the rename fails safely (nothing is
changed) if any session is still connected. Afterwards:
`docker compose restart backend worker beat` and check `/health/ready`.

**Verified 2026-10-10** (disposable Postgres 17 + the real migrations + seeded
rows; not production): encrypted and unencrypted backups end to end, restore to
a scratch database, restore onto an empty fresh cluster (role recreated), restore of a real development backup onto a server with a different owner role (86 migrations, 33 policies, matching row counts, per-tenant isolation checked across every RLS table), wrong
key / missing key / corrupted file all rejected, retries and recorded failures
for a permission error, an unreachable database and low disk, retention
decisions compared against an independent implementation on 25 synthetic dated
files, healthcheck, and the alert rules via `promtool`. This proves the
mechanism; it is not a substitute for periodically restoring your actual
production backups.

### Known limitations

- A restore runs as a shell command outside the Django application (by design;
  it must work when the application is down), so it is not written to the
  app's `AuditLog`. The backup container's logs and shell history are the
  record.
- `BACKUP_ENCRYPTION_KEY` has no rotation tooling: rotate by decrypting old
  backups with the old key and re-encrypting with the new one.
- Backups live on the same Docker host as the database unless you copy the
  `postgres-backups` volume elsewhere. That is an off-host copy you must
  arrange; the service does not do it.
- `core_backuprecord` grows by one row per attempt and has no pruning.

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
  against a disposable stack 2026-10-10; re-verify against whatever backup
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
