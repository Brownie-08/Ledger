"""OpenTelemetry tracing — OFF unless OTEL_ENABLED=1.

OTEL_EXPORTER=otlp (default; honours OTEL_EXPORTER_OTLP_ENDPOINT) or console.
"""

import os

_configured = False


def setup_tracing(service_name="ledger-wallet-api"):
    global _configured
    if _configured or os.environ.get("OTEL_ENABLED", "0") != "1":
        return False
    from opentelemetry import trace
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if os.environ.get("OTEL_EXPORTER", "otlp") == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    DjangoInstrumentor().instrument()
    _configured = True
    return True
