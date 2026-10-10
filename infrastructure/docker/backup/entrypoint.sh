#!/bin/sh
# Periodic, verified, optionally encrypted PostgreSQL backups with tiered
# (daily/weekly/monthly) retention. Runs in the `backup` service (see
# infrastructure/docker/backup/Dockerfile — postgres:17-alpine + openssl, so
# pg_dump/pg_restore already match the server they back up). See
# docs/deployment.md#backups for the restore procedure.
#
# Modes (first argument):
#   run    (default) back up now, prune, then repeat every BACKUP_INTERVAL_SECONDS
#   once   one backup + prune, then exit with the backup's status (for tests/cron)
#   prune  retention pass only (BACKUP_PRUNE_DRY_RUN=true lists, never deletes)
set -eu
umask 077

MODE="${1:-run}"
DEST_DIR="${BACKUP_DEST_DIR:-/backups}"
DAILY_RETENTION_DAYS="${BACKUP_DAILY_RETENTION_DAYS:-7}"
WEEKLY_RETENTION_WEEKS="${BACKUP_WEEKLY_RETENTION_WEEKS:-4}"
MONTHLY_RETENTION_MONTHS="${BACKUP_MONTHLY_RETENTION_MONTHS:-6}"
INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
RETRY_ATTEMPTS="${BACKUP_RETRY_ATTEMPTS:-3}"
RETRY_DELAY_SECONDS="${BACKUP_RETRY_DELAY_SECONDS:-60}"
DB_WAIT_SECONDS="${BACKUP_DB_WAIT_SECONDS:-180}"
ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"
PRUNE_DRY_RUN="${BACKUP_PRUNE_DRY_RUN:-false}"

# A typo in a retention value must stop the service loudly, not silently
# change what gets deleted.
for pair in DAILY_RETENTION_DAYS WEEKLY_RETENTION_WEEKS MONTHLY_RETENTION_MONTHS \
            INTERVAL_SECONDS RETRY_ATTEMPTS RETRY_DELAY_SECONDS DB_WAIT_SECONDS; do
  eval "val=\$$pair"
  case "$val" in
    ''|*[!0-9]*) echo "[backup] FATAL: ${pair}='${val}' is not a non-negative integer" >&2; exit 2 ;;
  esac
done
[ "$RETRY_ATTEMPTS" -ge 1 ] || { echo "[backup] FATAL: BACKUP_RETRY_ATTEMPTS must be >= 1" >&2; exit 2; }
[ "$INTERVAL_SECONDS" -ge 1 ] || { echo "[backup] FATAL: BACKUP_INTERVAL_SECONDS must be >= 1" >&2; exit 2; }

mkdir -p "$DEST_DIR"
chmod 700 "$DEST_DIR"

WORK_FILES=""
cleanup() {
  for f in $WORK_FILES; do rm -f -- "$f"; done
}
trap cleanup EXIT
track() { WORK_FILES="$WORK_FILES $*"; }

# started_at/finished_at/status/... are read by record_backup() as plain shell
# variables (not positional params), so there is no POSIX $10+ limit.
record_backup() {
  size_sql="${size_bytes:-NULL}"
  dfree_sql="${disk_free:-NULL}"
  dtotal_sql="${disk_total:-NULL}"
  # psql's `-v name=value` + `:'name'` (quoted) / `:name` (raw, for numbers
  # and the NULL keyword) substitution does the escaping — never hand-rolled
  # string interpolation into SQL. This only works for a script read with
  # -f; -c "..." does not perform it, hence the file.
  sql_file="$(mktemp)"
  track "$sql_file"
  cat >"$sql_file" <<'SQL'
INSERT INTO core_backuprecord
    (started_at, finished_at, status, file_path, file_size_bytes, sha256,
     encrypted, error_message, disk_free_bytes, disk_total_bytes, created_at)
VALUES (:'started', :'finished', :'st', :'path', :sz, :'sha', :enc, :'err', :df, :dt, now());
SQL
  psql -h "$PGHOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -v ON_ERROR_STOP=1 \
    -v started="$started_at" -v finished="$finished_at" -v st="$status" \
    -v path="$file_path" -v sha="$sha256" -v enc="$encrypted" -v err="$error_message" \
    -v sz="$size_sql" -v df="$dfree_sql" -v dt="$dtotal_sql" \
    -f "$sql_file" \
    || echo "[backup] WARNING: could not record backup status in the database (the backup file itself is unaffected)" >&2
  rm -f "$sql_file"
}

wait_for_database() {
  waited=0
  until pg_isready -q -h "$PGHOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do
    if [ "$waited" -ge "$DB_WAIT_SECONDS" ]; then
      echo "[backup] WARNING: database still not accepting connections after ${waited}s; attempting anyway" >&2
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
}

# The status table is created by the application's migrations, which may
# finish after this container starts. Waiting once avoids losing the very
# first record (the backup file is never held back for it).
wait_for_status_table() {
  waited=0
  while [ "$waited" -lt "$DB_WAIT_SECONDS" ]; do
    found="$(psql -h "$PGHOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA \
      -c "SELECT to_regclass('public.core_backuprecord') IS NOT NULL;" 2>/dev/null || true)"
    [ "$found" = "t" ] && return 0
    sleep 5
    waited=$((waited + 5))
  done
  echo "[backup] WARNING: core_backuprecord not found after ${waited}s; attempts will run but may not be recorded" >&2
}

latest_backup_file() {
  find "$DEST_DIR" -maxdepth 1 \( -name "${POSTGRES_DB}-*.dump" -o -name "${POSTGRES_DB}-*.dump.enc" \) | sort | tail -n1
}

# One attempt. Sets $status to succeeded|failed and records it.
run_backup() {
  started_at="$(date -u -Iseconds)"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  base_path="${DEST_DIR}/${POSTGRES_DB}-${timestamp}.dump"
  file_path="$base_path"
  tmp="${base_path}.tmp"
  enc_tmp="${base_path}.enc.tmp"
  err_file="$(mktemp)"
  track "$tmp" "$enc_tmp" "$err_file"
  status="failed"
  error_message=""
  size_bytes=""
  sha256=""
  encrypted="false"

  disk_free="$(df -kP "$DEST_DIR" | awk 'NR==2{print $4*1024}')"
  disk_total="$(df -kP "$DEST_DIR" | awk 'NR==2{print $2*1024}')"

  # Peak usage while encrypting is plaintext + ciphertext. Use the previous
  # backup as the size estimate; refuse early rather than fill the volume
  # (which would also make the next attempts fail).
  prev="$(latest_backup_file)"
  if [ -n "$prev" ]; then
    prev_size="$(wc -c <"$prev")"
    if [ "$disk_free" -lt $((prev_size * 2)) ]; then
      error_message="insufficient free space on the backup volume: ${disk_free} bytes free, need about $((prev_size * 2)) (2 x the previous backup). Free space or shorten retention."
    fi
  fi

  if [ -z "$error_message" ]; then
    echo "[backup] ${started_at} starting dump -> ${base_path}"
    # Custom format: pg_dump's own consistent-snapshot (MVCC) transaction,
    # no application downtime, structurally verifiable with
    # `pg_restore --list` and restorable selectively.
    if ! pg_dump -h "$PGHOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file="$tmp" 2>"$err_file"; then
      error_message="pg_dump failed: $(tail -n 3 "$err_file" | tr '\n' ' ' | cut -c1-800)"
    elif ! pg_restore --list "$tmp" >/dev/null 2>"$err_file"; then
      error_message="pg_restore --list could not read the fresh dump (treated as corrupt): $(tail -n 2 "$err_file" | tr '\n' ' ' | cut -c1-500)"
    fi
  fi

  if [ -z "$error_message" ]; then
    if [ -n "$ENCRYPTION_KEY" ]; then
      file_path="${base_path}.enc"
      if ! openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:ENCRYPTION_KEY -in "$tmp" -out "$enc_tmp" 2>"$err_file"; then
        error_message="encryption failed: $(tail -n 2 "$err_file" | tr '\n' ' ' | cut -c1-300)"
      else
        # Prove the ciphertext decrypts back to exactly the verified dump
        # BEFORE the plaintext is deleted — an undecryptable "backup" is
        # worse than a failed one.
        plain_sha="$(sha256sum "$tmp" | awk '{print $1}')"
        round_sha="$(openssl enc -d -aes-256-cbc -pbkdf2 -pass env:ENCRYPTION_KEY -in "$enc_tmp" 2>/dev/null | sha256sum | awk '{print $1}')"
        if [ "$plain_sha" != "$round_sha" ]; then
          error_message="encrypted backup did not decrypt back to the original dump (round-trip checksum mismatch)"
        else
          mv "$enc_tmp" "$file_path"
          encrypted="true"
        fi
      fi
      rm -f "$tmp"
    else
      file_path="$base_path"
      mv "$tmp" "$file_path"
      echo "[backup] WARNING: BACKUP_ENCRYPTION_KEY is not set - this backup is stored UNENCRYPTED" >&2
    fi
  fi

  if [ -z "$error_message" ]; then
    chmod 600 "$file_path"
    size_bytes="$(wc -c <"$file_path")"
    sha256="$(sha256sum "$file_path" | awk '{print $1}')"
    printf '%s  %s\n' "$sha256" "$(basename "$file_path")" >"${file_path}.sha256"
    status="succeeded"
    date -u +%s >"${DEST_DIR}/.last_success"
    echo "[backup] $(date -u -Iseconds) OK: ${file_path} (${size_bytes} bytes, sha256=${sha256}, encrypted=${encrypted})"
  else
    file_path=""
    echo "[backup] $(date -u -Iseconds) FAILED: ${error_message} - prior backups left untouched" >&2
  fi

  finished_at="$(date -u -Iseconds)"
  date -u +%s >"${DEST_DIR}/.last_attempt"
  record_backup
  cleanup
  WORK_FILES=""
  [ "$status" = "succeeded" ]
}

# Up to RETRY_ATTEMPTS attempts per cycle: the common failure is the database
# not being reachable yet (e.g. host/Docker restart), which clears itself.
run_backup_with_retry() {
  attempt=1
  while :; do
    if run_backup; then return 0; fi
    if [ "$attempt" -ge "$RETRY_ATTEMPTS" ]; then return 1; fi
    echo "[backup] attempt ${attempt}/${RETRY_ATTEMPTS} failed; retrying in ${RETRY_DELAY_SECONDS}s" >&2
    attempt=$((attempt + 1))
    sleep "$RETRY_DELAY_SECONDS"
    wait_for_database
  done
}

# Tiered retention (GFS-style): every backup from the last N days is kept;
# beyond that, one per rolling 7-day bucket for W weeks (a fixed-width window
# from the Unix epoch, not a calendar week - the aim is bounding the count by
# periodic thinning, not calendar-aligned reporting), then one per calendar
# month for M months; anything older is removed. The single most recent
# backup is never removed however the retention settings are configured, so
# a misconfiguration can never leave zero backups. Only ever touches this
# service's own `<db>-<timestamp>.dump[.enc]` files and their `.sha256`
# sidecars; temp files and anything else in the directory are ignored.
prune_backups() {
  echo "[backup] pruning (daily=${DAILY_RETENTION_DAYS}d weekly=${WEEKLY_RETENTION_WEEKS}w monthly=${MONTHLY_RETENTION_MONTHS}mo dry_run=${PRUNE_DRY_RUN})"
  now_epoch="${PRUNE_NOW_EPOCH:-$(date -u +%s)}"
  latest_ok="$(latest_backup_file)"
  list_file="${DEST_DIR}/.prune-list"
  del_file="${DEST_DIR}/.prune-delete"
  track "$list_file" "$del_file"

  find "$DEST_DIR" -maxdepth 1 \( -name "${POSTGRES_DB}-*.dump" -o -name "${POSTGRES_DB}-*.dump.enc" \) -print |
    while IFS= read -r f; do
      base="$(basename "$f")"
      ts="$(echo "$base" | sed -n "s/^${POSTGRES_DB}-\([0-9]\{8\}\)T\([0-9]\{6\}\)Z\.dump\(\.enc\)\{0,1\}$/\1 \2/p")"
      [ -n "$ts" ] || continue
      d="${ts%% *}"
      t="${ts#* }"
      y=$(echo "$d" | cut -c1-4); mo=$(echo "$d" | cut -c5-6); da=$(echo "$d" | cut -c7-8)
      hh=$(echo "$t" | cut -c1-2); mi=$(echo "$t" | cut -c3-4); ss=$(echo "$t" | cut -c5-6)
      epoch="$(date -u -d "${y}-${mo}-${da} ${hh}:${mi}:${ss}" +%s 2>/dev/null || true)"
      [ -n "$epoch" ] || continue
      printf '%s\t%s\t%s%s\n' "$epoch" "$f" "$y" "$mo"
    done | sort -n >"$list_file"

  awk -F'\t' -v now="$now_epoch" -v daily="$DAILY_RETENTION_DAYS" \
      -v weekly="$WEEKLY_RETENTION_WEEKS" -v monthly="$MONTHLY_RETENTION_MONTHS" -v keep="$latest_ok" '
    function age_days(epoch) { return int((now - epoch) / 86400) }
    {
      epoch=$1; path=$2; monthkey=$3
      age = age_days(epoch)
      weekcut = daily + weekly*7
      monthcut = weekcut + monthly*30
      if (path == keep) { decision[path] = "keep"; next }
      if (age <= daily) { decision[path] = "keep"; next }
      if (age <= weekcut) {
        key = "w" int(epoch / 604800)
        if (!(key in best) || epoch > best[key]) { best[key] = epoch; bestpath[key] = path }
        bucket[path] = key
        decision[path] = "pending"
        next
      }
      if (age <= monthcut) {
        key = "m" monthkey
        if (!(key in best) || epoch > best[key]) { best[key] = epoch; bestpath[key] = path }
        bucket[path] = key
        decision[path] = "pending"
        next
      }
      decision[path] = "delete"
    }
    END {
      for (p in decision) {
        if (decision[p] == "pending") {
          decision[p] = (bestpath[bucket[p]] == p) ? "keep" : "delete"
        }
        if (decision[p] == "delete") print p
      }
    }
  ' "$list_file" >"$del_file"

  while IFS= read -r victim; do
    [ -n "$victim" ] || continue
    if [ "$PRUNE_DRY_RUN" = "true" ]; then
      echo "[backup] would remove (outside retention): ${victim}"
    else
      echo "[backup] removing (outside retention): ${victim}"
      rm -f -- "$victim" "${victim}.sha256"
    fi
  done <"$del_file"
  rm -f "$list_file" "$del_file"
}

if [ "$MODE" = "prune" ]; then
  prune_backups
  exit 0
fi

echo "[backup] starting: mode=${MODE} interval=${INTERVAL_SECONDS}s daily=${DAILY_RETENTION_DAYS}d weekly=${WEEKLY_RETENTION_WEEKS}w monthly=${MONTHLY_RETENTION_MONTHS}mo retries=${RETRY_ATTEMPTS} dest=${DEST_DIR}"
if [ -z "$ENCRYPTION_KEY" ]; then
  echo "[backup] WARNING: BACKUP_ENCRYPTION_KEY is not set. Backups will be stored UNENCRYPTED until it is." >&2
fi
export ENCRYPTION_KEY

wait_for_database
wait_for_status_table

if [ "$MODE" = "once" ]; then
  rc=0
  run_backup_with_retry || rc=1
  prune_backups
  exit "$rc"
fi

# Back up once immediately (don't wait a full interval after a fresh start),
# then on the configured interval. A failed cycle never ends the loop.
while true; do
  run_backup_with_retry || echo "[backup] cycle failed after ${RETRY_ATTEMPTS} attempt(s); next cycle in ${INTERVAL_SECONDS}s" >&2
  prune_backups
  sleep "$INTERVAL_SECONDS"
done
