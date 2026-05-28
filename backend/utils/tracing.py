"""
Задача 28 — OpenTelemetry трассировка. Экспорт в Jaeger через OTLP.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from config.settings import settings

_tracer: Optional[trace.Tracer] = None


def setup_tracing() -> None:
    global _tracer
    resource = Resource.create({"service.name": "telegram-bot"})
    provider = TracerProvider(resource=resource)

    if settings.jaeger_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.jaeger_endpoint)
    else:
        exporter = ConsoleSpanExporter()  # fallback for local dev

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("telegram-bot")


def get_tracer() -> trace.Tracer:
    global _tracer
    if _tracer is None:
        setup_tracing()
    return _tracer


@contextmanager
def traced_span(name: str, **attrs: str) -> Generator[trace.Span, None, None]:
    """Context manager for a named span with optional attributes."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        for k, v in attrs.items():
            span.set_attribute(k, str(v))
        yield span
