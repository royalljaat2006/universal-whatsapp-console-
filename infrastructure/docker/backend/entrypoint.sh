#!/bin/sh
# Container entrypoint. Migrations run here ONLY when explicitly requested
# (local development). In staging/production, migrate is a separate release
# step so several web replicas never race to migrate the same database.
set -eu

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "entrypoint: applying migrations"
  python manage.py migrate --noinput
fi

exec "$@"
