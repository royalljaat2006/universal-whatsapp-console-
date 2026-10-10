# postgres:17-alpine already carries the matching pg_dump/pg_restore for the
# server it backs up. The only thing it's missing for this service is
# `openssl` (encryption at rest) - everything else (awk, sha256sum, psql,
# pg_isready) is already in the base image.
FROM postgres:17-alpine
RUN apk add --no-cache openssl
COPY entrypoint.sh restore.sh healthcheck.sh /
RUN chmod +x /entrypoint.sh /restore.sh /healthcheck.sh
ENTRYPOINT ["/entrypoint.sh"]
