"""Authenticated daily recovery for hosts without a continuously running beat."""

import secrets

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def maintenance(request):
    secret = settings.CRON_SECRET
    if len(secret) < 32 or not secrets.compare_digest(
        request.headers.get("Authorization", "").encode(), f"Bearer {secret}".encode()
    ):
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    from .tasks import expire_holds, nightly_reconciliation, publish_dispatches, publish_outbox

    # A publish failure returns 503 so operations can detect and retry the sweep.
    # The operations themselves tolerate repeated delivery.
    try:
        for task in (publish_dispatches, expire_holds, publish_outbox, nightly_reconciliation):
            task.delay()
    except Exception:
        return JsonResponse({"detail": "Maintenance dispatch unavailable"}, status=503)
    return JsonResponse({"queued": True})
