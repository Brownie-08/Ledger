"""Prometheus metrics. Exposed at /metrics (single-process registry; run gunicorn with threads)."""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
LEDGER_TRANSACTIONS = Counter("ledger_transactions_posted_total", "Posted ledger transactions", ["type"])
WEBHOOKS = Counter("webhook_events_total", "Provider webhook deliveries received", ["result"])
RECONCILIATION_MISMATCHES = Counter("reconciliation_mismatches_total", "Mismatches found by reconciliation", ["kind"])
