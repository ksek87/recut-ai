"""
OpenTelemetry SpanProcessor that ingests spans as recut traces.

Usage::

    from recut.providers.otel_ingester import RecutSpanProcessor
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    provider.add_span_processor(RecutSpanProcessor(agent_id="my-service"))
    trace.set_tracer_provider(provider)

Reads OpenInference semantic convention attributes first
(``output.value``, ``llm.output_messages.0.message.content``), then
falls back to generic OTel span names. Compatible with any OTel SDK via
duck typing — the ``opentelemetry`` package is not a hard dependency.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from recut.schema.trace import RecutStep, RecutTrace, StepType, TraceLanguage, TraceMeta, TraceMode

_log = logging.getLogger(__name__)

_OPENINFERENCE_KIND_MAP: dict[str, StepType] = {
    "LLM": StepType.OUTPUT,
    "TOOL": StepType.TOOL_CALL,
    "AGENT": StepType.OUTPUT,
    "CHAIN": StepType.OUTPUT,
    "RETRIEVER": StepType.TOOL_RESULT,
}

_OPENINFERENCE_CONTENT_ATTRS = (
    "output.value",
    "llm.output_messages.0.message.content",
    "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments",
)


def _extract_content(span: Any) -> str:
    attrs = span.attributes or {}
    for key in _OPENINFERENCE_CONTENT_ATTRS:
        if key in attrs:
            return str(attrs[key])
    return span.name


def _span_to_step(span: Any, index: int) -> RecutStep:
    attrs = span.attributes or {}
    kind_str = str(attrs.get("openinference.span.kind", "LLM"))
    step_type = _OPENINFERENCE_KIND_MAP.get(kind_str, StepType.OUTPUT)
    return RecutStep(index=index, type=step_type, content=_extract_content(span))


class RecutSpanProcessor:
    """
    OTel SpanProcessor that groups spans by trace_id and persists as RecutTrace.

    Compatible with any OTel SDK via duck typing — no opentelemetry dependency
    is required at runtime. The processor is registered with a TracerProvider
    and receives spans as they end.
    """

    def __init__(
        self,
        agent_id: str = "otel",
        mode: TraceMode | str = TraceMode.PEEK,
    ) -> None:
        self._agent_id = agent_id
        self._mode = TraceMode(mode) if isinstance(mode, str) else mode
        self._traces: dict[int, list[Any]] = {}

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        pass

    def on_end(self, span: Any) -> None:
        ctx = span.context
        if ctx is None:
            return

        trace_id = ctx.trace_id
        spans = self._traces.setdefault(trace_id, [])
        spans.append(span)

        parent = getattr(span, "parent", None)
        is_root = parent is None or not getattr(parent, "is_valid", True)
        if not is_root:
            return

        steps = [_span_to_step(s, i) for i, s in enumerate(spans)]
        attrs = span.attributes or {}
        model = str(attrs.get("llm.model_name", "unknown"))
        prompt = str(attrs.get("input.value", ""))
        trace_obj = RecutTrace(
            agent_id=self._agent_id,
            prompt=prompt,
            mode=self._mode,
            language=TraceLanguage.SIMPLE,
            meta=TraceMeta(model=model, provider="otel", total_steps=len(steps)),
        )
        trace_obj.steps.extend(steps)
        del self._traces[trace_id]
        self._persist(trace_obj)

    def _persist(self, trace_obj: RecutTrace) -> None:
        from recut.core.tracer import _persist_trace

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_persist_trace(trace_obj))
            else:
                loop.run_until_complete(_persist_trace(trace_obj))
        except Exception as exc:
            _log.debug("recut: OTel ingester persist failed: %s", exc)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self) -> None:
        self._traces.clear()
