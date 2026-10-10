#!/bin/sh
# Healthy while a backup succeeded within 2 intervals + 1h. Written by
# entrypoint.sh (.last_success = epoch seconds of the last good backup).
f="${BACKUP_DEST_DIR:-/backups}/.last_success"
[ -f "$f" ] || { echo "no successful backup recorded yet" >&2; exit 1; }
age=$(( $(date -u +%s) - $(cat "$f") ))
limit=$(( 2 * ${BACKUP_INTERVAL_SECONDS:-86400} + 3600 ))
[ "$age" -le "$limit" ] || { echo "last successful backup was ${age}s ago (limit ${limit}s)" >&2; exit 1; }
