# syntax=docker/dockerfile:1.7
# One image, several roles (web / worker / beat) selected by the command.

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app

# ---- development: all dependency groups, source bind-mounted by compose ----
FROM base AS development
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY backend/ ./
COPY infrastructure/docker/backend/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
USER app
ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- production: runtime dependencies only, non-root, static collected ----
FROM base AS production
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev
COPY backend/ ./
COPY infrastructure/docker/backend/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && DJANGO_SETTINGS_MODULE=config.settings.production \
       DJANGO_SECRET_KEY=collectstatic-only-not-a-real-key-0000000000000000000000 \
       DJANGO_ALLOWED_HOSTS=localhost DATABASE_URL=postgres://u:p@localhost/db \
       python manage.py collectstatic --noinput \
    && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).status == 200 else 1)"
ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
