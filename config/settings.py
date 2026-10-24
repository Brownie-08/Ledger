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
    "ledger",
    "provider",
]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "config.urls"
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

CELERY_BEAT_SCHEDULE = {
    "expire-holds": {"task": "ledger.tasks.expire_holds", "schedule": 60.0},
    "publish-outbox": {"task": "ledger.tasks.publish_outbox", "schedule": 10.0},
    "nightly-reconciliation": {"task": "ledger.tasks.nightly_reconciliation", "schedule": crontab(hour=2, minute=0)},
}
