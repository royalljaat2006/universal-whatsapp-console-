#!/bin/sh
# Periodic PostgreSQL backups. Runs in the `backup` service (postgres:17-alpine
# image — pg_dump is already the right version for this server, no extra
# install). See docs/deployment.md#backups for the restore procedure and the
# restore test this was actually verified against.
set -eu

RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
DEST_DIR="/backups"

mkdir -p "$DEST_DIR"

run_backup() {
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dest="${DEST_DIR}/${POSTGRES_DB}-${timestamp}.sql.gz"
  tmp="${dest}.tmp"
  echo "[backup] $(date -u -Iseconds) starting dump -> ${dest}"
  if pg_dump -h "${PGHOST}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --format=plain \
      | gzip > "${tmp}"; then
    mv "${tmp}" "${dest}"
    size="$(du -h "${dest}" | cut -f1)"
    echo "[backup] $(date -u -Iseconds) OK: ${dest} (${size})"
  else
    status=$?
    echo "[backup] $(date -u -Iseconds) FAILED (exit ${status}) — leaving prior backups untouched" >&2
    rm -f "${tmp}"
  fi
  echo "[backup] pruning backups older than ${RETENTION_DAYS} day(s)"
  find "$DEST_DIR" -name "*.sql.gz" -mtime "+${RETENTION_DAYS}" -print -delete
}

echo "[backup] starting: interval=${INTERVAL_SECONDS}s retention=${RETENTION_DAYS}d dest=${DEST_DIR}"
# Back up once immediately (don't wait a full interval after a fresh start),
# then on the configured interval.
while true; do
  run_backup
  sleep "$INTERVAL_SECONDS"
done
