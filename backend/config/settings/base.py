"""Base settings shared by every environment.

All environment-dependent values are read here, once, through django-environ.
Environment modules (development/test/staging/production) only override
behaviour; they never introduce new configuration keys.
"""

from pathlib import Path
from typing import Any

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Local convenience only: containers receive env vars from compose / the
# orchestrator. A missing file is not an error.
environ.Env.read_env(BASE_DIR.parent / ".env", overwrite=False)

APP_ENV = env.str("APP_ENV", default="development")
SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
    "channels",
]
# Domain modules of the modular monolith. Order matters only for migrations
# that depend on each other; Django resolves that from migration graphs.
LOCAL_APPS = [
    "core",
    "apps.identity",
    "apps.organizations",
    "apps.apikeys",
    "apps.businesses",
    "apps.teams",
    "apps.audit",
    "apps.whatsapp",
    "apps.contacts",
    "apps.conversations",
    "apps.templates",
    "apps.media",
    "apps.messaging",
    "apps.campaigns",
    "apps.billing",
    "apps.analytics",
    "apps.reports",
    "apps.notifications",
    "apps.webhooks",
    "apps.realtime",
    "apps.platform",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

AUTH_USER_MODEL = "identity.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MIDDLEWARE = [
    "core.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.AccessLogMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Data stores
# ---------------------------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"].setdefault("connect_timeout", 5)

REDIS_CACHE_URL = env.str("REDIS_CACHE_URL", default="redis://localhost:6379/0")
CHANNELS_REDIS_URL = env.str("CHANNELS_REDIS_URL", default="redis://localhost:6379/2")
RATE_LIMIT_REDIS_URL = env.str("RATE_LIMIT_REDIS_URL", default="redis://localhost:6379/3")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [CHANNELS_REDIS_URL], "capacity": 1500, "expiry": 30},
    }
}

# ---------------------------------------------------------------------------
# Auth & sessions
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12
CSRF_COOKIE_SAMESITE = "Lax"
# The SPA reads the CSRF cookie to echo it in X-CSRFToken.
CSRF_COOKIE_HTTPONLY = False

# ---------------------------------------------------------------------------
# I18N / static
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media bytes: private storage, never served directly (apps/media/storage.py).
# Production: point "media" at an S3-compatible backend via STORAGES.
MEDIA_ROOT = env.path("MEDIA_ROOT", default=BASE_DIR / "media")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "media": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(MEDIA_ROOT), "base_url": None},
    },
}
# Uploads larger than this spool to a temp file instead of memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "apps.apikeys.authentication.ApiKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "core.api.pagination.StandardCursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": ["rest_framework.filters.OrderingFilter"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "core.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    # Documented in docs/architecture/rate-limiting (docs/api.md#rate-limits).
    "DEFAULT_THROTTLE_RATES": {
        "user": env.str("THROTTLE_USER", default="1200/min"),
        "anon": env.str("THROTTLE_ANON", default="60/min"),
        "auth": env.str("THROTTLE_AUTH", default="10/min"),
        "send": env.str("THROTTLE_SEND", default="600/min"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "WhatsApp Platform API",
    "VERSION": "1.0.0",
    "DESCRIPTION": "Versioned REST API (/api/v1/). See docs/api.md for conventions.",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
}

# ---------------------------------------------------------------------------
# Celery (see workers/queues.py for queue topology)
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_TASK_ALWAYS_EAGER = False

# ---------------------------------------------------------------------------
# Meta WhatsApp Cloud API — the ONLY place the Graph version is configured.
# ---------------------------------------------------------------------------
META_GRAPH_API_VERSION = env.str("META_GRAPH_API_VERSION", default="v26.0")
META_GRAPH_API_BASE_URL = env.str("META_GRAPH_API_BASE_URL", default="https://graph.facebook.com")
META_HTTP_TIMEOUT_SECONDS = env.float("META_HTTP_TIMEOUT_SECONDS", default=15.0)
META_APP_ID = env.str("META_APP_ID", default="")
META_APP_SECRET = env.str("META_APP_SECRET", default="")
META_WEBHOOK_VERIFY_TOKEN = env.str("META_WEBHOOK_VERIFY_TOKEN", default="")
META_ACCESS_TOKEN_REFERENCE = env.str("META_ACCESS_TOKEN_REFERENCE", default="")
# Hosts Meta serves media downloads from. Media URLs outside this list are
# never fetched (SSRF guard). Verified host: lookaside.fbsbx.com.
META_MEDIA_DOWNLOAD_HOSTS = env.list("META_MEDIA_DOWNLOAD_HOSTS", default=["lookaside.fbsbx.com"])

# Secrets resolvable through `env:` credential references must carry this prefix.
PROVIDER_SECRET_ENV_PREFIX = "PROVIDER_SECRET_"  # noqa: S105 - a name prefix, not a secret
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEYS", default=[])

# ---------------------------------------------------------------------------
# Webhooks / messaging
# ---------------------------------------------------------------------------
WEBHOOK_MAX_BODY_BYTES = env.int("WEBHOOK_MAX_BODY_BYTES", default=3 * 1024 * 1024)
WEBHOOK_MAX_PROCESSING_ATTEMPTS = env.int("WEBHOOK_MAX_PROCESSING_ATTEMPTS", default=8)
# A status webhook can arrive before the send transaction commits its
# provider id; unmatched statuses are retried for this long before being
# recorded as unmatched.
WEBHOOK_UNMATCHED_STATUS_RETRY_SECONDS = env.int(
    "WEBHOOK_UNMATCHED_STATUS_RETRY_SECONDS", default=600
)
# Messages stuck in SENDING longer than this are failed as outcome-unknown
# (never re-sent automatically — see docs/messaging-architecture.md).
MESSAGE_SENDING_STUCK_SECONDS = env.int("MESSAGE_SENDING_STUCK_SECONDS", default=600)

# Campaigns (Build Cycle 3, docs/campaign-architecture.md)
CAMPAIGN_MAX_RECIPIENTS = env.int("CAMPAIGN_MAX_RECIPIENTS", default=200_000)
CAMPAIGN_DEFAULT_RATE_PER_MINUTE = env.int("CAMPAIGN_DEFAULT_RATE_PER_MINUTE", default=60)
CAMPAIGN_MAX_RATE_PER_MINUTE = env.int("CAMPAIGN_MAX_RATE_PER_MINUTE", default=1200)
# One execution tick dispatches rate * tick / 60 recipients.
CAMPAIGN_TICK_SECONDS = env.int("CAMPAIGN_TICK_SECONDS", default=5)
# A RUNNING campaign without a tick for this long is re-kicked by beat.
CAMPAIGN_STALE_TICK_SECONDS = env.int("CAMPAIGN_STALE_TICK_SECONDS", default=120)
# Ticks re-schedule themselves (disabled in tests, which drive ticks directly).
CAMPAIGN_AUTO_TICK = env.bool("CAMPAIGN_AUTO_TICK", default=True)
# Default per-number outbound throughput (Meta default: 80 msg/s per number).
WHATSAPP_DEFAULT_THROUGHPUT_MPS = env.int("WHATSAPP_DEFAULT_THROUGHPUT_MPS", default=80)
# Optional error-tracking adapter, "module:function" (core/observability.py).
ERROR_TRACKING_HOOK = env.str("ERROR_TRACKING_HOOK", default="")
# Redis token bucket per sending number (core/ratelimit.py).
OUTBOUND_THROTTLE_ENABLED = env.bool("OUTBOUND_THROTTLE_ENABLED", default=True)
# WhatsApp customer service window (hours) for free-form messages.
WHATSAPP_SERVICE_WINDOW_HOURS = 24

DATA_UPLOAD_MAX_MEMORY_SIZE = WEBHOOK_MAX_BODY_BYTES

# ---------------------------------------------------------------------------
# Retention (see docs/security.md#data-retention). Nothing is purged unless
# RETENTION_ENABLED is true.
# ---------------------------------------------------------------------------
RETENTION_ENABLED = env.bool("RETENTION_ENABLED", default=False)
RETENTION_WEBHOOK_EVENTS_DAYS = env.int("RETENTION_WEBHOOK_EVENTS_DAYS", default=90)
RETENTION_AUDIT_LOGS_DAYS = env.int("RETENTION_AUDIT_LOGS_DAYS", default=0)

# ---------------------------------------------------------------------------
# Transactional email (password reset, email verification, invitations).
# EMAIL_BACKEND defaults to the console backend: emails are logged, not sent
# — no provider decision has been made yet (see
# docs/productization/customer-onboarding.md). Point EMAIL_BACKEND at
# django.core.mail.backends.smtp.EmailBackend + the EMAIL_HOST_* vars once a
# provider is chosen; no application code changes needed.
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env.str("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env.str("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env.str("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env.str("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="no-reply@example.com")

# Absolute origin (scheme + host + any subpath mount point, no trailing
# slash) used only where a link must work outside the app shell — email
# bodies. Everything else (API responses) returns paths and lets the
# frontend combine them with its own origin/BASE_PATH (see
# lib/api/client.ts) since the browser already knows both.
PUBLIC_BASE_URL = env.str("PUBLIC_BASE_URL", default="http://localhost:8080").rstrip("/")

# ---------------------------------------------------------------------------
# Logging — structured, context-enriched, secret-redacting.
# ---------------------------------------------------------------------------
LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
LOG_FORMAT = env.str("LOG_FORMAT", default="json")

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "context": {"()": "core.logging.ContextFilter"},
    },
    "formatters": {
        "json": {"()": "core.logging.JsonFormatter"},
        "console": {"()": "core.logging.ConsoleFormatter"},
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "filters": ["context"],
            "formatter": LOG_FORMAT if LOG_FORMAT in {"json", "console"} else "json",
        },
    },
    "root": {"handlers": ["stdout"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["stdout"], "level": "WARNING", "propagate": False},
        "django.request": {"handlers": ["stdout"], "level": "ERROR", "propagate": False},
        "celery": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "httpx": {"handlers": ["stdout"], "level": "WARNING", "propagate": False},
    },
}
