import hashlib
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me-in-production-0123456789")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
if not DEBUG and (len(SECRET_KEY) < 32 or SECRET_KEY.startswith("dev-insecure") or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        "Production requires a random SECRET_KEY of at least 32 characters and explicit ALLOWED_HOSTS"
    )
if not DEBUG and len(SECRET_KEY) < 50:
    # Render generates 256-bit base64 secrets (44 characters). Preserve their
    # entropy while normalising the representation to Django's key-length check.
    SECRET_KEY = hashlib.sha512(SECRET_KEY.encode()).hexdigest()

SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "0" if DEBUG else "1") == "1"
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if os.environ.get("TRUST_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
MOCK_PROVIDER_ENABLED = os.environ.get("MOCK_PROVIDER_ENABLED", "1" if DEBUG else "0") == "1"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "ledger",
    "provider",
]
MIDDLEWARE = [
    "ledger.observability.RequestIDMiddleware",
    "ledger.observability.MetricsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "ledger"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}
if os.environ.get("DATABASE_URL"):
    db_url = urlparse(os.environ["DATABASE_URL"])
    if db_url.scheme not in {"postgres", "postgresql"} or not db_url.hostname or not db_url.path.strip("/"):
        raise ImproperlyConfigured("DATABASE_URL must be a PostgreSQL connection URL")
    DATABASES["default"].update(
        {
            "NAME": unquote(db_url.path.lstrip("/")),
            "USER": unquote(db_url.username or ""),
            "PASSWORD": unquote(db_url.password or ""),
            "HOST": db_url.hostname,
            "PORT": db_url.port or 5432,
            "OPTIONS": {
                "sslmode": parse_qs(db_url.query).get("sslmode", ["prefer"])[0],
                **{
                    key: values[0]
                    for key, values in parse_qs(db_url.query).items()
                    if key in {"channel_binding", "sslrootcert", "connect_timeout"}
                },
            },
        }
    )
DATABASES["default"]["CONN_MAX_AGE"] = int(os.environ.get("DB_CONN_MAX_AGE", "0"))
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["ledger.throttling.AnonThrottle", "ledger.throttling.UserThrottle"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Ledger Wallet API",
    "DESCRIPTION": "Double-entry payments ledger: wallets, transfers, FX, holds, refunds, withdrawals, webhooks. "
    "Amounts are integers in minor units. All money-moving POSTs require an Idempotency-Key header.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {"CurrencyEnum": "ledger.models.Currency"},
}

LOG_FORMAT = os.environ.get("LOG_FORMAT", "json")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "ledger.observability.RequestIdFilter"}},
    "formatters": {
        "json": {"()": "ledger.observability.JsonFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": LOG_FORMAT, "filters": ["request_id"]},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {"django.request": {"level": "ERROR"}},  # 4xx are expected business outcomes; we log our own
}
LEDGER_THROTTLE_RATES = {
    "anon": os.environ.get("THROTTLE_ANON", "30/min"),
    "user": os.environ.get("THROTTLE_USER", "600/min"),
    "money": os.environ.get("THROTTLE_MONEY", "120/min"),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.environ.get("JWT_ACCESS_MINUTES", "15"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "SIGNING_KEY": SECRET_KEY,
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
if os.environ.get("REDIS_URL") and os.environ.get("USE_REDIS_CACHE", "0") == "1":
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": os.environ["REDIS_URL"]}
    }

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_DEFAULT_QUEUE = os.environ.get("CELERY_QUEUE", "celery")
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_EAGER", "0") == "1"
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_PUBLISH_RETRY = False
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}
if CELERY_BROKER_URL == "vercel://":
    CELERY_BROKER_TRANSPORT_OPTIONS = {"lease_duration": 300, "requeue_delay_seconds": 10}
    # Business state is durable in PostgreSQL; no ephemeral result backend needed.
    CELERY_TASK_IGNORE_RESULT = True

CRON_SECRET = os.environ.get("CRON_SECRET", "")

# --- Mock payment provider (week 3) ---
PROVIDER_WEBHOOK_SECRET = os.environ.get("PROVIDER_WEBHOOK_SECRET", "whsec_dev_secret")
PROVIDER_WEBHOOK_URL = os.environ.get("PROVIDER_WEBHOOK_URL", "http://localhost:8000/api/webhooks/provider/")
if os.environ.get("PROVIDER_WEBHOOK_BASE_URL"):
    PROVIDER_WEBHOOK_URL = os.environ["PROVIDER_WEBHOOK_BASE_URL"].rstrip("/") + "/api/webhooks/provider/"
PROVIDER_WEBHOOK_TOLERANCE_SECONDS = int(os.environ.get("PROVIDER_WEBHOOK_TOLERANCE_SECONDS", "300"))

# --- Fees (week 4). Basis points of the transfer amount, min/max in minor units (0 = no cap).
TRANSFER_FEE_BPS = int(os.environ.get("TRANSFER_FEE_BPS", "50"))  # 0.5%
TRANSFER_FEE_MIN = int(os.environ.get("TRANSFER_FEE_MIN", "0"))
TRANSFER_FEE_MAX = int(os.environ.get("TRANSFER_FEE_MAX", "0"))

# --- KYC tiers. Amounts in minor units. Tier 0 = signed up, unverified; tier 1 = admin-approved.
KYC_ENFORCED = os.environ.get("KYC_ENFORCED", "1") == "1"
KYC_TIERS = {
    0: {
        "currencies": ["NGN"],
        "max_balance": {"NGN": 5_000_000, "USD": 3_500, "GBP": 2_800},  # NGN 50,000 / $35 / GBP 28
        "daily_outflow": {"NGN": 2_000_000, "USD": 1_400, "GBP": 1_100},  # NGN 20,000 / $14 / GBP 11
        "withdrawals": False,
        "fx": False,
        "virtual_accounts": False,
    },
    1: {
        "currencies": ["NGN", "USD", "GBP"],
        "max_balance": {"NGN": 1_000_000_000, "USD": 700_000, "GBP": 560_000},  # NGN 10m / $7,000 / GBP 5,600
        "daily_outflow": {"NGN": 500_000_000, "USD": 350_000, "GBP": 280_000},  # NGN 5m / $3,500 / GBP 2,800
        "withdrawals": True,
        "fx": True,
        "virtual_accounts": True,
    },
}
VIRTUAL_ACCOUNT_SECRET = os.environ.get("VIRTUAL_ACCOUNT_SECRET", "va-dev-secret")
if not DEBUG and (len(PROVIDER_WEBHOOK_SECRET) < 32 or len(VIRTUAL_ACCOUNT_SECRET) < 32):
    raise ImproperlyConfigured(
        "Production requires independent random provider and virtual-account secrets of at least 32 characters"
    )

CELERY_BEAT_SCHEDULE = {
    "publish-dispatches": {"task": "ledger.tasks.publish_dispatches", "schedule": 10.0},
    "expire-holds": {"task": "ledger.tasks.expire_holds", "schedule": 60.0},
    "publish-outbox": {"task": "ledger.tasks.publish_outbox", "schedule": 10.0},
    "nightly-reconciliation": {"task": "ledger.tasks.nightly_reconciliation", "schedule": crontab(hour=2, minute=0)},
}
