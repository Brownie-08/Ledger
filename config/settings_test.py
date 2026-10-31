from .settings import *  # noqa: F403

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
SECURE_SSL_REDIRECT = False
MOCK_PROVIDER_ENABLED = True
ALLOWED_HOSTS = [*ALLOWED_HOSTS, "testserver"]  # noqa: F405

SECRET_KEY = "test-only-secret-key-that-is-long-enough-for-hs256"
SIMPLE_JWT = {**SIMPLE_JWT, "SIGNING_KEY": SECRET_KEY}  # noqa: F405
