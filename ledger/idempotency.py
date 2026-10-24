import hashlib
import json
from functools import wraps

from django.db import IntegrityError, transaction
from rest_framework.response import Response

from .models import IdempotencyKey


def idempotent(scope):
    """Require Idempotency-Key on a DRF view method; replay stored responses."""

    def deco(fn):
        @wraps(fn)
        def wrapper(self, request, *args, **kwargs):
            key = request.headers.get("Idempotency-Key")
            if not key:
                return Response({"error": "missing_idempotency_key"}, status=400)
            body = json.dumps({"data": request.data, "kwargs": kwargs}, sort_keys=True, default=str)
            scope_key = f"{scope}:{getattr(request.user, 'pk', None) or 'anon'}"  # keys are per-user
            req_hash = hashlib.sha256(body.encode()).hexdigest()
            stored = IdempotencyKey.objects.filter(key=key, scope=scope_key).first()
            if stored:
                if stored.request_hash != req_hash:
                    return Response({"error": "idempotency_key_reused_with_different_body"}, status=422)
                resp = Response(stored.response_body, status=stored.status_code)
                resp["Idempotent-Replayed"] = "true"
                return resp
            request.idempotency_key = f"{scope_key}:{key}"
            response = fn(self, request, *args, **kwargs)
            if response.status_code < 500:
                try:
                    with transaction.atomic():
                        IdempotencyKey.objects.create(
                            key=key,
                            scope=scope_key,
                            request_hash=req_hash,
                            response_body=response.data,
                            status_code=response.status_code,
                        )
                except IntegrityError:
                    pass
            return response

        return wrapper

    return deco
