# Backup and disaster recovery

**Status: not automated. This cycle did not change that.**

`docs/deployment.md`'s go-live checklist lists "Database backups verified by
a restore test" as a manual, one-time gate before launch — there is no
scheduled backup job, no retention policy enforcement, and no documented
restore runbook in this repository. This is a real gap, not something Cycle
4 was asked to close (backup/DR automation requires infrastructure decisions
— where backups are stored, retention duration, encryption-at-rest for the
backup artifact itself — that weren't part of this cycle's scope).

## What exists today

- PostgreSQL is the single source of truth for all tenant data (shared
  schema, `organization_id`-scoped — see `tenant-isolation.md`). Standard
  `pg_dump`/`pg_basebackup` + WAL archiving applies; nothing about the
  multi-tenant model requires a different backup strategy than a
  single-tenant Postgres deployment.
- Redis holds the Celery broker, cache, rate-limit counters and the realtime
  channel layer — all reconstructible/disposable state, not a backup target.
  Queued-but-unprocessed Celery tasks would be lost on a Redis failure; this
  is a known trade-off already accepted by the existing architecture
  (`docker-compose.yml`'s comment on AOF persistence mitigates but does not
  eliminate this).
- Uploaded media (`apps/media/`) lives on whatever storage backend
  `STORAGES["media"]` points to in production (filesystem in local dev,
  presumably object storage in production — not verified against the actual
  production configuration in this cycle).

## What a real backup/DR runbook needs before launch

1. A scheduled `pg_dump` or continuous WAL-archiving job, with retention.
2. A documented, periodically-tested restore procedure (the go-live checklist
   already requires "verified by a restore test" — that test has not been
   run as part of this cycle).
3. A decision on media backup (depends on the production storage backend,
   not verified here).
4. Encryption-at-rest for backup artifacts, matching the same discipline
   already applied to `FIELD_ENCRYPTION_KEYS` and provider credentials
   (`docs/security.md`).
5. An application-level rollback procedure — `docs/deployment.md`'s release
   procedure already separates migration from deploy for exactly this
   reason (a bad migration can be rolled back independently of the code).

None of the above was implemented in this cycle; it is scoped here so it is
not silently skipped in a later "production-ready" claim.
