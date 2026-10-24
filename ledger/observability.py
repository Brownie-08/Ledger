"""Request ids, structured JSON logging, and HTTP metrics middleware."""

import json
import logging
import re
import time
import uuid

from django.db import connection
from django.http import HttpResponse, JsonResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .context import request_id_var
from .metrics import HTTP_LATENCY, HTTP_REQUESTS

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class RequestIDMiddleware:
    """Accepts an incoming X-Request-ID (if sane) or generates one; echoes it back and tags logs/audit."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        request.request_id = rid
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response["X-Request-ID"] = rid
        return response


class MetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()
        response = self.get_response(request)
        match = getattr(request, "resolver_match", None)
        route = "/" + match.route if match and match.route else "unmatched"
        HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - start)
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        return response


def metrics_view(request):
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


def healthz(request):
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return JsonResponse({"ok": True})


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        try:  # attach trace ids when OpenTelemetry is active
            from opentelemetry import trace

            ctx = trace.get_current_span().get_span_context()
            if ctx.is_valid:
                payload["trace_id"] = f"{ctx.trace_id:032x}"
                payload["span_id"] = f"{ctx.span_id:016x}"
        except Exception:  # pragma: no cover
            pass
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and key not in payload and key != "request_id":
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


JsonFormatter.converter = time.gmtime
