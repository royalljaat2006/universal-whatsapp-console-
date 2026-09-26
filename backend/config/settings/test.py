"""Test settings: real PostgreSQL (constraints, JSONB and partial indexes must be
exercised for real), no network, eager Celery, in-memory channel layer."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-for-any-real-environment")
os.environ.setdefault(
    "DATABASE_URL",
    "postgres://whatsapp_platform:change-me-dev-only@localhost:5432/whatsapp_platform",
)

# Tests run on the host against the compose Postgres/Redis (127.0.0.1) or
# inside CI service containers; override via real env vars when needed.
os.environ.setdefault("RATE_LIMIT_REDIS_URL", "redis://localhost:6379/3")

from .base import *

APP_ENV = "test"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SESSION_ENGINE = "django.contrib.sessions.backends.db"

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
OUTBOUND_THROTTLE_ENABLED = False
CAMPAIGN_AUTO_TICK = False

# Deterministic Meta configuration. Tests must never reach graph.facebook.com;
# the provider's HTTP transport is replaced in tests (see tests/conftest.py).
META_APP_ID = "test-app-id"
META_APP_SECRET = "test-app-secret"
META_WEBHOOK_VERIFY_TOKEN = "test-verify-token"
META_GRAPH_API_BASE_URL = "https://graph.facebook.com"
META_GRAPH_API_VERSION = "v26.0"
META_EMBEDDED_SIGNUP_CONFIG_ID = "test-config-id"
FIELD_ENCRYPTION_KEYS = ["pZ1jz3kqGJ8Vqf0cS8tT7p9k7Yx2HqQ0b3cJ6gq4b1Y="]

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
}

LOG_FORMAT = "console"
LOGGING["handlers"]["stdout"]["formatter"] = "console"
LOGGING["root"]["level"] = "WARNING"
