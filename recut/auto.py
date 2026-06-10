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

# provider name -> (patched class, original create method), used for uninstall()
_originals: dict[str, tuple[type, Any]] = {}

# Strong references so fire-and-forget capture tasks aren't garbage-collected
# mid-flight (see asyncio.create_task docs).
_bg_tasks: set[asyncio.Task] = set()


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


def _patch_targets() -> list[tuple[str, type, str]]:
    """Return (provider, class to patch, response attr that marks non-streaming)."""
    targets: list[tuple[str, type, str]] = []
    try:
        from anthropic.resources.messages import AsyncMessages

        targets.append(("anthropic", AsyncMessages, "content"))
    except ImportError:
        pass
    try:
        from openai.resources.chat.completions import AsyncCompletions

        targets.append(("openai", AsyncCompletions, "choices"))
    except ImportError:
        pass
    return targets


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
        if provider == "anthropic":
            from recut.providers.anthropic import parse_response_to_steps as parse_fn
        else:
            from recut.providers.openai import parse_response_to_steps as parse_fn

        model = str(kwargs.get("model") or getattr(response, "model", "unknown"))
        steps = parse_fn(response, model=model)
        if not steps:
            return
        trace_obj = _build_trace(agent_id, mode, model, provider, kwargs, steps)
        await write_queue.enqueue(_persist_trace(trace_obj))
    except Exception as exc:
        _log.debug("recut: %s auto-capture error: %s", provider, exc)


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
