"""
recut.init() — zero-change SDK instrumentation.

Patches Anthropic and OpenAI async clients so every messages.create /
chat.completions.create call is captured as a recut trace — no agent
code changes required.

Usage::

    import recut
    recut.init(agent_id="my-service")

    # Your existing agent code runs unchanged:
    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(...)  # captured automatically

Streaming calls are passed through without capture.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import random
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from recut.core.tracer import _persist_trace
from recut.schema.trace import RecutTrace, TraceLanguage, TraceMeta, TraceMode
from recut.storage import write_queue
from recut.utils import parse_float_env

_log = logging.getLogger(__name__)

# provider name -> (patched class, original create method), used for uninstall()
_originals: dict[str, tuple[type, Any]] = {}

# Strong references so fire-and-forget capture tasks aren't garbage-collected
# mid-flight (see asyncio.create_task docs).
_bg_tasks: set[asyncio.Task] = set()

# Active run grouping: recut.run() sets the contextvar; captures inside the
# context append to one shared trace instead of creating one trace per call.
_current_run: contextvars.ContextVar[str | None] = contextvars.ContextVar("recut_run", default=None)
_active_runs: OrderedDict[str, RecutTrace] = OrderedDict()
# Capture tasks can land after the run() block exits, so entries are evicted
# LRU-style rather than on context exit.
_MAX_ACTIVE_RUNS = 128


def init(
    agent_id: str | None = None,
    mode: TraceMode | str = TraceMode.PEEK,
    sample_rate: float = 1.0,
) -> None:
    """
    Patch Anthropic and OpenAI SDK clients to capture traces automatically.

    Parameters
    ----------
    agent_id:
        Label for all captured traces. Falls back to RECUT_AGENT_ID env var,
        then "auto".
    mode:
        Trace mode — "peek" (default) or "audit".
    sample_rate:
        Fraction of calls to capture (0.0–1.0). Overridden by
        RECUT_DEFAULT_SAMPLE_RATE env var.
    """
    _agent_id = agent_id or os.environ.get("RECUT_AGENT_ID", "auto")
    _mode = TraceMode(mode) if isinstance(mode, str) else mode
    for provider, target_cls, response_attr in _patch_targets():
        _install_wrapper(provider, target_cls, response_attr, _agent_id, _mode, sample_rate)


def uninstall() -> None:
    """Restore the original SDK methods patched by init()."""
    for target_cls, original in _originals.values():
        target_cls.create = original  # type: ignore[attr-defined]
    _originals.clear()
    _active_runs.clear()


@contextmanager
def run(run_id: str | None = None) -> Iterator[str]:
    """
    Group all auto-captured LLM calls inside this block into a single trace.

    Without this, recut.init() records one trace per SDK call. Wrapping an
    agent run groups every call into one multi-step trace whose id is the
    returned run_id::

        recut.init(agent_id="my-service")
        with recut.run() as run_id:
            await my_agent.handle(request)   # N calls -> 1 trace
        # recut peek <run_id>

    run_id must be unique per run — reusing one overwrites the prior trace.
    """
    rid = run_id or str(uuid.uuid4())
    token = _current_run.set(rid)
    try:
        yield rid
    finally:
        _current_run.reset(token)


def _patch_targets() -> list[tuple[str, type, str]]:
    """Return (provider, class to patch, response attr) from all registered providers."""
    from recut.providers.registry import get_registered, load_providers

    load_providers()
    return [(name, *p.patch_target()) for name, p in get_registered().items()]


def _install_wrapper(
    provider: str,
    target_cls: type,
    response_attr: str,
    agent_id: str,
    mode: TraceMode,
    sample_rate: float,
) -> None:
    if provider in _originals:
        return
    original = target_cls.create  # type: ignore[attr-defined]

    @functools.wraps(original)
    async def _wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        response = await original(self, *args, **kwargs)
        effective_rate = parse_float_env("RECUT_DEFAULT_SAMPLE_RATE", sample_rate)
        if random.random() <= effective_rate and hasattr(response, response_attr):
            task = asyncio.create_task(_capture(response, kwargs, agent_id, mode, provider))
            _bg_tasks.add(task)
            task.add_done_callback(_bg_tasks.discard)
        return response

    target_cls.create = _wrapped  # type: ignore[attr-defined]
    _originals[provider] = (target_cls, original)
    _log.debug("recut: %s SDK patched for auto-capture", provider)


async def _capture(
    response: Any,
    kwargs: dict,
    agent_id: str,
    mode: TraceMode,
    provider: str,
) -> None:
    try:
        from recut.providers.registry import get_provider

        provider_instance = get_provider(provider)
        if provider_instance is None:
            return
        model = str(kwargs.get("model") or getattr(response, "model", "unknown"))
        steps = provider_instance.parse_response(response, model=model)
        if not steps:
            return
        rid = _current_run.get()
        if rid is None:
            trace_obj = _build_trace(agent_id, mode, model, provider, kwargs, steps)
        else:
            trace_obj = _append_to_run(rid, agent_id, mode, model, provider, kwargs, steps)
        await write_queue.enqueue(_persist_trace(trace_obj))
    except Exception as exc:
        _log.debug("recut: %s auto-capture error: %s", provider, exc)


def _append_to_run(
    rid: str,
    agent_id: str,
    mode: TraceMode,
    model: str,
    provider: str,
    kwargs: dict,
    steps: list,
) -> RecutTrace:
    """Get or create the shared trace for a run and append this call's steps."""
    trace_obj = _active_runs.get(rid)
    if trace_obj is None:
        trace_obj = _build_trace(agent_id, mode, model, provider, kwargs, steps)
        trace_obj.id = rid
        _active_runs[rid] = trace_obj
        if len(_active_runs) > _MAX_ACTIVE_RUNS:
            _active_runs.popitem(last=False)
    else:
        offset = len(trace_obj.steps)
        for i, step in enumerate(steps):
            step.index = offset + i
        trace_obj.steps.extend(steps)
        trace_obj.meta.total_steps = len(trace_obj.steps)
    return trace_obj


def _build_trace(
    agent_id: str,
    mode: TraceMode,
    model: str,
    provider: str,
    kwargs: dict,
    steps: list,
) -> RecutTrace:
    prompt = _extract_prompt(kwargs.get("messages", []))
    trace_obj = RecutTrace(
        agent_id=agent_id,
        prompt=prompt,
        mode=mode,
        language=TraceLanguage.SIMPLE,
        meta=TraceMeta(model=model, provider=provider, total_steps=len(steps)),
    )
    trace_obj.steps.extend(steps)
    return trace_obj


def _extract_prompt(messages: list) -> str:
    """Return the last user message content as a plain string."""
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
    return ""
