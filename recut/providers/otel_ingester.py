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

Threading model
---------------
``on_end`` is called synchronously on whichever thread ends the span — it
must not block or raise (OTel spec).  All mutation here is protected by
``_lock`` and uses only non-blocking deque operations.  Assembled traces
are buffered in ``_ready``; async persistence is scheduled onto any
running event loop, or flushed synchronously by ``force_flush()``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any

from recut.core.tracer import _coerce_mode, _persist_trace
from recut.schema.trace import RecutStep, RecutTrace, StepType, TraceLanguage, TraceMeta, TraceMode
from recut.storage import write_queue

_log = logging.getLogger(__name__)

_MAX_INCOMPLETE_TRACES = 512
_MAX_READY_TRACES = 512

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
    return str(span.name)


def _span_to_step(span: Any, index: int) -> RecutStep:
    attrs = span.attributes or {}
    kind_str = str(attrs.get("openinference.span.kind", "LLM"))
    step_type = _OPENINFERENCE_KIND_MAP.get(kind_str, StepType.OUTPUT)
    return RecutStep(index=index, type=step_type, content=_extract_content(span))


def _build_trace(
    spans: list[Any],
    root_span: Any,
    agent_id: str,
    mode: TraceMode,
) -> RecutTrace:
    steps = [_span_to_step(s, i) for i, s in enumerate(spans)]
    attrs = root_span.attributes or {}
    trace_obj = RecutTrace(
        agent_id=agent_id,
        prompt=str(attrs.get("input.value", "")),
        mode=mode,
        language=TraceLanguage.SIMPLE,
        meta=TraceMeta(
            model=str(attrs.get("llm.model_name", "unknown")),
            provider="otel",
            total_steps=len(steps),
        ),
    )
    trace_obj.steps.extend(steps)
    return trace_obj


class RecutSpanProcessor:
    """
    OTel SpanProcessor that groups spans by trace_id and persists as RecutTrace.

    Thread-safe: ``on_end`` only does non-blocking synchronous work under a
    lock.  Assembled traces are buffered in a bounded deque and drained
    asynchronously.  Call ``await processor.drain()`` or rely on
    ``force_flush()`` (called by the OTel SDK on shutdown) to ensure delivery.
    """

    def __init__(
        self,
        agent_id: str = "otel",
        mode: TraceMode | str = TraceMode.PEEK,
    ) -> None:
        self._agent_id = agent_id
        self._mode = _coerce_mode(mode)
        self._incomplete: dict[int, list[Any]] = {}
        self._ready: deque[RecutTrace] = deque(maxlen=_MAX_READY_TRACES)
        self._lock = threading.Lock()
        self._bg_tasks: set[asyncio.Task] = set()

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        pass

    def on_end(self, span: Any) -> None:
        trace_obj = self._try_complete(span)
        if trace_obj is not None:
            self._ready.append(trace_obj)
            self._schedule_drain()

    def _try_complete(self, span: Any) -> RecutTrace | None:
        ctx = span.context
        if ctx is None:
            return None

        with self._lock:
            trace_id = ctx.trace_id
            spans = self._incomplete.setdefault(trace_id, [])
            spans.append(span)

            if len(self._incomplete) > _MAX_INCOMPLETE_TRACES:
                self._incomplete.pop(next(iter(self._incomplete)))

            parent = getattr(span, "parent", None)
            is_root = parent is None or not getattr(parent, "is_valid", True)
            if not is_root:
                return None

            complete_spans = self._incomplete.pop(trace_id)

        return _build_trace(complete_spans, span, self._agent_id, self._mode)

    def _schedule_drain(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from a non-async thread. Trace stays buffered in _ready
            # until drain() or force_flush() is called.
            return
        task = loop.create_task(self._drain())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _drain(self) -> None:
        while self._ready:
            trace_obj = self._ready.popleft()
            await write_queue.enqueue(_persist_trace(trace_obj))

    async def drain(self) -> None:
        """Flush all buffered traces. Call from an async context."""
        await self._drain()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Synchronously flush buffered traces. Called by the OTel SDK on shutdown."""
        if not self._ready:
            return True
        try:
            loop = asyncio.get_running_loop()
            # Running inside an async context — schedule and wait from another thread.
            future = asyncio.run_coroutine_threadsafe(self._drain(), loop)
            future.result(timeout=timeout_millis / 1000)
        except RuntimeError:
            # No running event loop — create a temporary one.
            asyncio.run(self._drain())
        except Exception as exc:
            _log.debug("recut: OTel force_flush failed: %s", exc)
        return True

    def shutdown(self) -> None:
        self._incomplete.clear()
        self._ready.clear()
