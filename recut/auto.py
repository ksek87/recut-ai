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
import functools
import logging
import os
import random
from typing import Any

from recut.core.tracer import _persist_trace
from recut.schema.trace import RecutTrace, TraceLanguage, TraceMeta, TraceMode
from recut.storage import write_queue
from recut.utils import parse_float_env

_log = logging.getLogger(__name__)

_patched: set[str] = set()


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
    _patch_anthropic(_agent_id, _mode, sample_rate)
    _patch_openai(_agent_id, _mode, sample_rate)


def _patch_anthropic(agent_id: str, mode: TraceMode, sample_rate: float) -> None:
    if "anthropic" in _patched:
        return
    try:
        from anthropic.resources.messages import AsyncMessages
    except ImportError:
        return

    original = AsyncMessages.create

    @functools.wraps(original)
    async def _wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        response = await original(self, *args, **kwargs)
        effective_rate = parse_float_env("RECUT_DEFAULT_SAMPLE_RATE", sample_rate)
        if random.random() <= effective_rate and hasattr(response, "content"):
            asyncio.create_task(_capture_anthropic(response, kwargs, agent_id, mode))
        return response

    AsyncMessages.create = _wrapped  # type: ignore[method-assign]
    _patched.add("anthropic")
    _log.debug("recut: anthropic SDK patched for auto-capture")


def _patch_openai(agent_id: str, mode: TraceMode, sample_rate: float) -> None:
    if "openai" in _patched:
        return
    try:
        from openai.resources.chat.completions import AsyncCompletions
    except ImportError:
        return

    original = AsyncCompletions.create

    @functools.wraps(original)
    async def _wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        response = await original(self, *args, **kwargs)
        effective_rate = parse_float_env("RECUT_DEFAULT_SAMPLE_RATE", sample_rate)
        if random.random() <= effective_rate and hasattr(response, "choices"):
            asyncio.create_task(_capture_openai(response, kwargs, agent_id, mode))
        return response

    AsyncCompletions.create = _wrapped  # type: ignore[method-assign]
    _patched.add("openai")
    _log.debug("recut: openai SDK patched for auto-capture")


async def _capture_anthropic(
    response: Any,
    kwargs: dict,
    agent_id: str,
    mode: TraceMode,
) -> None:
    try:
        from recut.providers.anthropic import _parse_response_to_steps

        model = str(kwargs.get("model") or getattr(response, "model", "unknown"))
        steps = _parse_response_to_steps(response, model=model)
        if not steps:
            return
        trace_obj = _build_trace(agent_id, mode, model, "anthropic", kwargs, steps)
        await write_queue.enqueue(_persist_trace(trace_obj))
    except Exception as exc:
        _log.debug("recut: anthropic auto-capture error: %s", exc)


async def _capture_openai(
    response: Any,
    kwargs: dict,
    agent_id: str,
    mode: TraceMode,
) -> None:
    try:
        from recut.providers.openai import _parse_openai_response_to_steps

        model = str(kwargs.get("model") or getattr(response, "model", "unknown"))
        steps = _parse_openai_response_to_steps(response, model=model)
        if not steps:
            return
        trace_obj = _build_trace(agent_id, mode, model, "openai", kwargs, steps)
        await write_queue.enqueue(_persist_trace(trace_obj))
    except Exception as exc:
        _log.debug("recut: openai auto-capture error: %s", exc)


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
