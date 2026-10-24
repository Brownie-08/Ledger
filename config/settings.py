import os
from datetime import timedelta
from pathlib import Path

from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me-in-production-0123456789")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "drf_spectacular",
    "ledger",
    "provider",
]
MIDDLEWARE = [
    "ledger.observability.RequestIDMiddleware",
    "ledger.observability.MetricsMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "OPTIONS": {}}]
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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["ledger.throttling.AnonThrottle", "ledger.throttling.UserThrottle"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
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

CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_EAGER", "0") == "1"
CELERY_TASK_EAGER_PROPAGATES = True

# --- Mock payment provider (week 3) ---
PROVIDER_WEBHOOK_SECRET = os.environ.get("PROVIDER_WEBHOOK_SECRET", "whsec_dev_secret")
PROVIDER_WEBHOOK_URL = os.environ.get("PROVIDER_WEBHOOK_URL", "http://localhost:8000/api/webhooks/provider/")
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

CELERY_BEAT_SCHEDULE = {
    "expire-holds": {"task": "ledger.tasks.expire_holds", "schedule": 60.0},
    "publish-outbox": {"task": "ledger.tasks.publish_outbox", "schedule": 10.0},
    "nightly-reconciliation": {"task": "ledger.tasks.nightly_reconciliation", "schedule": crontab(hour=2, minute=0)},
}
