"""Vercel's private queue subscriber; the Redis worker remains available locally."""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from config.celery import app  # noqa: E402

app.autodiscover_tasks(["ledger", "provider"], force=True)

__all__ = ["app"]
