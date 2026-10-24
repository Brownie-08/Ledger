import json
import logging
import os
import subprocess
import sys

import pytest
from django.test import Client

from ledger import services
from ledger.observability import JsonFormatter, RequestIdFilter

pytestmark = pytest.mark.django_db


def test_request_id_generated_and_echoed():
    c = Client()
    r = c.get("/healthz")
    assert r.status_code == 200 and len(r["X-Request-ID"]) == 32
    r2 = c.get("/healthz", HTTP_X_REQUEST_ID="abc-123")
    assert r2["X-Request-ID"] == "abc-123"
    r3 = c.get("/healthz", HTTP_X_REQUEST_ID="bad id\nwith newline")  # rejected, regenerated
    assert r3["X-Request-ID"] != "bad id\nwith newline" and len(r3["X-Request-ID"]) == 32


def test_json_log_line_has_request_id_and_extras():
    from ledger.context import request_id_var

    token = request_id_var.set("req-42")
    try:
        rec = logging.LogRecord("ledger", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        rec.wallet_id = 7
        RequestIdFilter().filter(rec)
        line = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert line["msg"] == "hello world" and line["request_id"] == "req-42" and line["wallet_id"] == 7
    assert line["level"] == "INFO" and line["ts"].endswith("Z")


def test_audit_rows_carry_request_id(django_user_model, make_wallet):
    from rest_framework.test import APIClient

    from ledger.models import AuditLog

    a, b = make_wallet("a", balance=500), make_wallet("b")
    c = APIClient()
    c.force_authenticate(a.owner)
    r = c.post(
        "/api/transfers/",
        {"source_wallet": a.id, "destination_wallet": b.id, "amount": 100},
        format="json",
        HTTP_IDEMPOTENCY_KEY="t1",
        HTTP_X_REQUEST_ID="trace-me-1",
    )
    assert r.status_code == 201
    assert AuditLog.objects.get(action="transfer").request_id == "trace-me-1"


def test_metrics_endpoint(make_wallet, django_capture_on_commit_callbacks):
    a, b = make_wallet("a", balance=500), make_wallet("b")
    with django_capture_on_commit_callbacks(execute=True):
        services.transfer(a, b, 100, "m1")
    c = Client()
    c.get("/healthz")
    body = c.get("/metrics").content.decode()
    assert 'http_requests_total{method="GET",route="/healthz",status="200"}' in body
    assert "http_request_duration_seconds_bucket" in body
    assert 'ledger_transactions_posted_total{type="transfer"}' in body


def test_openapi_schema_and_docs():
    c = Client()
    r = c.get("/api/schema/?format=json")
    assert r.status_code == 200
    schema = json.loads(r.content)
    paths = schema["paths"]
    for p in ["/api/transfers/", "/api/wallets/{id}/holds/", "/api/auth/token/", "/api/admin/reconciliation/"]:
        assert p in paths, p
    assert "jwtAuth" in schema["components"]["securitySchemes"]
    assert c.get("/api/docs/").status_code == 200


def test_tracing_off_by_default():
    from config.otel import setup_tracing

    assert os.environ.get("OTEL_ENABLED", "0") != "1"
    assert setup_tracing() is False


def test_tracing_emits_spans_when_enabled():
    """Run in a subprocess so global OTel instrumentation doesn't leak into other tests."""
    code = (
        "import django, os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings_test'; django.setup();"
        "from config.otel import setup_tracing; assert setup_tracing();"
        "from django.test import Client; Client().get('/metrics')"
    )
    env = {**os.environ, "OTEL_ENABLED": "1", "OTEL_EXPORTER": "console"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert '"name": "GET metrics"' in out.stdout or '"http.route": "metrics"' in out.stdout, out.stdout[-2000:]
