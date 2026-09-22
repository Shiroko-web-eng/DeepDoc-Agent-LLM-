from __future__ import annotations

import logging
from typing import Any

from app.config import Settings


logger = logging.getLogger(__name__)


def configure_observability(settings: Settings, application: Any) -> None:
    if not settings.otel_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise RuntimeError("install the production extra to export OpenTelemetry") from exc
    provider = TracerProvider(resource=Resource.create({
        "service.name": "deepdoc-agent",
        "service.version": settings.service_version,
        "deployment.environment.name": settings.environment,
    }))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=settings.otel_endpoint.rstrip("/") + "/v1/traces"
    )))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(application, tracer_provider=provider)
    logger.info("opentelemetry_configured")
