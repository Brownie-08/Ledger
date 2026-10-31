import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from celery import Celery
from celery.app.task import Task


class DjangoThreadTask(Task):
    """Run synchronous Django tasks off Vercel's async subscriber thread."""

    def __call__(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return super().__call__(*args, **kwargs)

        request = vars(self.request).copy()

        def run():
            from django.db import close_old_connections

            self.push_request(**request)
            try:
                close_old_connections()
                return Task.__call__(self, *args, **kwargs)
            finally:
                close_old_connections()
                self.pop_request()

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(run).result()


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
vercel_broker = os.environ.get("CELERY_BROKER_URL") == "vercel://"
app = Celery(
    "ledger",
    task_cls=DjangoThreadTask if vercel_broker else None,
)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
