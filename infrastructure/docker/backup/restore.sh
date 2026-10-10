#!/bin/sh
# Verify or restore a backup produced by entrypoint.sh. Run INSIDE the
# `backup` container:
#   docker compose exec backup sh /restore.sh <file.dump[.enc]>
#
# Default mode (no second argument) restores into an ISOLATED scratch
# database on the same Postgres server, runs integrity checks, and drops the
# scratch database. It never touches the real database.
#
# Restoring into a named database needs BOTH a second argument (the exact
# target name) AND CONFIRM_PRODUCTION_RESTORE=yes. The backup is first
# restored into a staging database and checked; only then are the databases
# swapped by rename. The previous database is KEPT as
# <target>_pre_restore_<timestamp> (drop it yourself once satisfied), so a
# failed or wrong restore never destroys the data it was meant to replace.
# See docs/deployment.md#backups.
set -eu
umask 077

FILE="${1:?usage: restore.sh <backup-file> [target-db-name]}"
TARGET_DB="${2:-}"
STAMP="$(date -u +%Y%m%d%H%M%S)"
APP_ROLE="${DB_APP_ROLE:-whatsapp_platform_app}"
SCRATCH_DB=""
PLAIN=""
FILE_IS_PLAIN="yes"

# Tables compared between live and restored. Informational: the live
# database keeps changing after the backup was taken.
COUNT_TABLES="identity_user organizations_organization contacts_contact audit_auditlog"

pg() { psql -h "$PGHOST" -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 "$@"; }

cleanup() {
  if [ -n "$SCRATCH_DB" ]; then
    pg -d postgres -q -c "DROP DATABASE IF EXISTS \"${SCRATCH_DB}\";" >/dev/null 2>&1 || \
      echo "[restore] WARNING: could not drop ${SCRATCH_DB}; drop it manually" >&2
  fi
  if [ "$FILE_IS_PLAIN" = "no" ] && [ -n "$PLAIN" ]; then rm -f -- "$PLAIN"; fi
}
trap cleanup EXIT

case "$FILE" in
  /*) : ;;
  *) FILE="/backups/${FILE}" ;;
esac
[ -f "$FILE" ] || { echo "No such backup file: $FILE" >&2; exit 1; }

if [ -n "$TARGET_DB" ]; then
  case "$TARGET_DB" in
    *[!A-Za-z0-9_]*|'') echo "Target database name must match [A-Za-z0-9_]+" >&2; exit 1 ;;
  esac
  if [ "${CONFIRM_PRODUCTION_RESTORE:-}" != "yes" ]; then
    echo "Refusing to restore into '${TARGET_DB}': set CONFIRM_PRODUCTION_RESTORE=yes to proceed." >&2
    echo "Stop application traffic first. The current database is kept, renamed." >&2
    exit 1
  fi
fi

# 1. Checksum (detects bit rot / truncation of the stored file).
if [ -f "${FILE}.sha256" ]; then
  expected="$(awk '{print $1}' "${FILE}.sha256")"
  actual="$(sha256sum "$FILE" | awk '{print $1}')"
  if [ "$expected" != "$actual" ]; then
    echo "[restore] FAILED: sha256 mismatch for $FILE (expected $expected, got $actual)" >&2
    exit 1
  fi
  echo "[restore] sha256 matches the stored checksum"
else
  echo "[restore] WARNING: no ${FILE}.sha256 sidecar - file integrity not checked (older backup?)" >&2
fi

# 2. Decrypt into a private temp file (removed on exit, including on error).
PLAIN="$FILE"
case "$FILE" in
  *.enc)
    [ -n "${BACKUP_ENCRYPTION_KEY:-}" ] || { echo "BACKUP_ENCRYPTION_KEY is not set - cannot decrypt $FILE" >&2; exit 1; }
    PLAIN="$(mktemp)"
    FILE_IS_PLAIN="no"
    echo "[restore] decrypting ${FILE}"
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$FILE" -out "$PLAIN" \
      || { echo "[restore] FAILED: decryption failed (wrong BACKUP_ENCRYPTION_KEY or corrupt file)" >&2; exit 1; }
    ;;
esac

echo "[restore] verifying archive structure"
pg_restore --list "$PLAIN" >/dev/null || { echo "[restore] FAILED: not a readable pg_dump archive" >&2; exit 1; }

# 3. The RLS application role is cluster-level, so it is NOT inside a
# per-database dump. Without it the dump's GRANTs cannot be applied and every
# tenant request would fail with "permission denied" after a restore onto a
# fresh server. Recreate it idempotently (NOLOGIN NOBYPASSRLS, as the
# migration core.0001_row_level_security does) and grant membership.
pg -d postgres -q <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_ROLE}') THEN
    CREATE ROLE ${APP_ROLE} NOLOGIN NOBYPASSRLS;
  END IF;
END
\$\$;
GRANT ${APP_ROLE} TO "${POSTGRES_USER}";
SQL

# A dump taken under one owner role can reference that role (for example
# `ALTER DEFAULT PRIVILEGES FOR ROLE <owner> ...`) even with --no-owner. On a
# server where that role does not exist (new cluster, managed Postgres, a
# different POSTGRES_USER) the restore would abort. Create any such roles as
# NOLOGIN placeholders; they hold nothing and cannot connect.
ensure_referenced_roles() {
  pg_restore -s -f - "$PLAIN" 2>/dev/null |
    sed -n -e 's/^ALTER DEFAULT PRIVILEGES FOR ROLE \([A-Za-z0-9_]*\) .*/\1/p' \
           -e 's/^GRANT .* TO \([A-Za-z0-9_]*\);$/\1/p' \
           -e 's/^REVOKE .* FROM \([A-Za-z0-9_]*\);$/\1/p' | sort -u |
    while IFS= read -r role; do
      [ -n "$role" ] || continue
      case "$role" in PUBLIC|public|CURRENT_USER|"$POSTGRES_USER") continue ;; esac
      pg -d postgres -q -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${role}') THEN CREATE ROLE \"${role}\" NOLOGIN; END IF; END \$\$;"
    done
}

# Re-apply the app role's grants for the role that is restoring. Idempotent.
# Privileges inside the dump belong to the ORIGINAL owner; when the restoring
# role differs, tables created by FUTURE migrations (run by this role) would
# otherwise have no default grant for the app role and fail with "permission
# denied" after the next deploy.
reapply_app_grants() {
  pg -d "$1" -q <<SQL
GRANT USAGE ON SCHEMA public TO ${APP_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ${APP_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ${APP_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${APP_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ${APP_ROLE};
SQL
}

restore_into() {
  # Privileges are deliberately kept (no --no-privileges): they carry the
  # app role's table grants. --no-owner makes the restoring role the owner.
  ensure_referenced_roles
  pg_restore -h "$PGHOST" -U "$POSTGRES_USER" -d "$1" --no-owner --exit-on-error "$PLAIN"
  reapply_app_grants "$1"
}

scalar() { pg -d "$1" -tA -c "$2" | tr -d ' \n'; }

# Checks on a restored database. Prints findings; returns non-zero on FAIL.
check_restored() {
  db="$1"; failed=0
  mig="$(scalar "$db" "SELECT count(*) FROM django_migrations;")"
  if [ "${mig:-0}" -gt 0 ]; then echo "  django_migrations: ${mig} applied [OK]"; else echo "  django_migrations: empty [FAIL]"; failed=1; fi

  rls_live="$(scalar "$POSTGRES_DB" "SELECT count(*) FROM pg_policies WHERE schemaname='public';" 2>/dev/null)" || rls_live="n/a"
  rls_rest="$(scalar "$db" "SELECT count(*) FROM pg_policies WHERE schemaname='public';")"
  # Live may legitimately differ (migrations since the backup) or be empty
  # (disaster recovery onto a new server); the restored copy must have them.
  if [ "${rls_rest:-0}" -gt 0 ]; then echo "  RLS policies: restored=${rls_rest} (live=${rls_live}) [OK]"; else echo "  RLS policies: restored=0 (live=${rls_live}) [FAIL - tenant isolation policies missing]"; failed=1; fi

  # The app role must be able to query tenant tables AND, with no tenant
  # context set, must see no rows (fail closed).
  contacts="$(scalar "$db" "SELECT count(*) FROM contacts_contact;")"
  visible="$(pg -d "$db" -q -tA 2>&1 <<SQL | tr -d ' \n'
BEGIN;
SET LOCAL ROLE ${APP_ROLE};
SELECT count(*) FROM contacts_contact;
ROLLBACK;
SQL
)"
  case "$visible" in
    *[!0-9]*) echo "  app-role access: ${visible} [FAIL]"; failed=1 ;;
    *)
      if [ "$contacts" -gt 0 ] && [ "$visible" != "0" ]; then
        echo "  app-role access: sees ${visible}/${contacts} contacts with NO tenant context [FAIL - RLS not enforcing]"; failed=1
      else
        echo "  app-role access: query permitted; ${visible} of ${contacts} contacts visible without tenant context [OK]"
      fi ;;
  esac

  echo "  row counts, live vs restored (informational - live data may have changed since the backup):"
  for table in $COUNT_TABLES; do
    live="$(scalar "$POSTGRES_DB" "SELECT count(*) FROM ${table};" 2>/dev/null)" || live="n/a"
    [ -n "$live" ] || live="n/a"
    rest="$(scalar "$db" "SELECT count(*) FROM ${table};")" || rest="ERR"
    note=""
    [ "$live" = "$rest" ] || note=" (differs)"
    echo "    ${table}: live=${live} restored=${rest}${note}"
    [ "$rest" != "ERR" ] || failed=1
  done
  return "$failed"
}

if [ -z "$TARGET_DB" ]; then
  SCRATCH_DB="restore_check_${STAMP}"
  echo "[restore] creating isolated scratch database: ${SCRATCH_DB}"
  pg -d postgres -q -c "CREATE DATABASE \"${SCRATCH_DB}\";"
  restore_into "$SCRATCH_DB"
  echo "[restore] integrity checks on the restored copy:"
  if check_restored "$SCRATCH_DB"; then
    echo "[restore] verification PASSED. No production data was touched."
  else
    echo "[restore] verification FAILED - see [FAIL] lines above." >&2
    exit 1
  fi
  exit 0
fi

# Named-target restore: stage, check, then swap by rename.
SCRATCH_DB="${TARGET_DB}_restoring_${STAMP}"
echo "[restore] restoring into staging database ${SCRATCH_DB}"
pg -d postgres -q -c "CREATE DATABASE \"${SCRATCH_DB}\";"
restore_into "$SCRATCH_DB"
echo "[restore] integrity checks on the staged copy:"
check_restored "$SCRATCH_DB" || { echo "[restore] FAILED checks - '${TARGET_DB}' was NOT modified." >&2; exit 1; }

KEPT="${TARGET_DB}_pre_restore_${STAMP}"
exists="$(scalar postgres "SELECT count(*) FROM pg_database WHERE datname='${TARGET_DB}';")"
if [ "$exists" = "1" ]; then
  # Fails (leaving everything as it was) if any session is still connected.
  pg -d postgres -q -c "ALTER DATABASE \"${TARGET_DB}\" RENAME TO \"${KEPT}\";" \
    || { echo "[restore] FAILED: could not rename '${TARGET_DB}' - is application traffic stopped? Nothing was changed." >&2; exit 1; }
fi
if ! pg -d postgres -q -c "ALTER DATABASE \"${SCRATCH_DB}\" RENAME TO \"${TARGET_DB}\";"; then
  [ "$exists" = "1" ] && pg -d postgres -q -c "ALTER DATABASE \"${KEPT}\" RENAME TO \"${TARGET_DB}\";"
  echo "[restore] FAILED to promote the staged copy; the original '${TARGET_DB}' was put back." >&2
  exit 1
fi
SCRATCH_DB=""   # promoted: the cleanup trap must not drop it
echo "[restore] done. '${TARGET_DB}' now holds the restored backup."
[ "$exists" = "1" ] && echo "[restore] previous database kept as '${KEPT}' - drop it manually once you are satisfied."
echo "[restore] next: docker compose restart backend worker beat, then check /health/ready."
