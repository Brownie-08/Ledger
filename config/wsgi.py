import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from config.otel import setup_tracing  # noqa: E402

setup_tracing()
application = get_wsgi_application()
