"""
OpenTelemetry bridge for recut-ai.

Maps recut concepts → OTel spans per the spec in docs/product/INTEGRATIONS.md:
  RecutTrace  → root span  "recut.trace"
  RecutStep   → child span "recut.step"
  RecutFlag   → span event "recut.flag"
  StepReasoning → span attributes "recut.reasoning.*"

Usage:
    provider = setup_otel("my-agent")           # ConsoleSpanExporter by default
    emit_trace(trace, steps, flags_by_step)     # prints spans to stdout
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import Tracer

from recut.schema.trace import RecutFlag, RecutStep, RecutTrace


def setup_otel(
    service_name: str = "recut-demo",
    endpoint: str | None = None,
) -> TracerProvider:
    """
    Configure the OTel SDK.

    Returns a TracerProvider backed by ConsoleSpanExporter unless `endpoint` is
    set (e.g. "http://localhost:4317"), in which case OTLP/gRPC is used instead.
    """
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=endpoint)
    else:
        exporter = ConsoleSpanExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))

    from opentelemetry import trace

    trace.set_tracer_provider(provider)
    return provider


def emit_trace(
    trace_obj: RecutTrace,
    steps: list[RecutStep],
    flags_by_step: dict[str, list[RecutFlag]],
    tracer: Tracer | None = None,
) -> None:
    """Emit one root span per trace, one child span per step."""
    from opentelemetry import trace

    if tracer is None:
        tracer = trace.get_tracer("recut")

    total_flags = sum(len(v) for v in flags_by_step.values())

    with tracer.start_as_current_span("recut.trace") as root:
        root.set_attribute("recut.trace.id", trace_obj.id)
        root.set_attribute("recut.trace.agent_id", trace_obj.agent_id)
        root.set_attribute("recut.trace.mode", trace_obj.mode.value)
        root.set_attribute("recut.trace.prompt", trace_obj.prompt[:200])
        root.set_attribute("recut.trace.step_count", len(steps))
        root.set_attribute("recut.trace.flag_count", total_flags)

        for step in steps:
            _emit_step(step, flags_by_step.get(step.id, []), tracer)


def _emit_step(
    step: RecutStep,
    flags: list[RecutFlag],
    tracer: Tracer,
) -> None:
    from opentelemetry import trace

    ctx = trace.get_current_span().get_span_context()
    with tracer.start_as_current_span(
        "recut.step",
        context=trace.set_span_in_context(trace.get_current_span()),
    ) as span:
        span.set_attribute("recut.step.id", step.id)
        span.set_attribute("recut.step.index", step.index)
        span.set_attribute("recut.step.type", step.type.value)
        span.set_attribute("recut.step.content_preview", step.content[:200])
        span.set_attribute("recut.step.risk_score", step.risk_score)

        if step.reasoning:
            span.set_attribute("recut.reasoning.source", step.reasoning.source.value)
            span.set_attribute("recut.reasoning.confidence", step.reasoning.confidence)
            span.set_attribute("recut.reasoning.content_preview", step.reasoning.content[:200])

        for flag in flags:
            span.add_event(
                "recut.flag",
                attributes={
                    "flag.type": flag.type.value,
                    "flag.severity": flag.severity.value,
                    "flag.source": flag.source.value,
                    "flag.reason": flag.plain_reason[:200],
                    "flag.step_id": flag.step_id,
                },
            )

    _ = ctx  # suppress unused-variable warning — ctx used implicitly via OTel context
