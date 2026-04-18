from __future__ import annotations

import asyncio
import functools
import json
import os
import random
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from recut.providers.base import AbstractProvider
from recut.schema.trace import (
    RecutStep,
    RecutTrace,
    TraceLanguage,
    TraceMeta,
    TraceMode,
)
from recut.storage.circuit_breaker import is_open, record_failure, record_success
from recut.storage.db import StorageClient
from recut.storage.models import TraceRow


class RecutContext:
    """
    Live context for an in-flight trace.

    Holds the growing list of steps and exposes helpers so the flagging
    engine and hook system can read state without needing the full trace.
    """

    def __init__(
        self,
        trace: RecutTrace,
        provider: AbstractProvider,
        flag_handlers: list[Callable],
    ):
        self.trace = trace
        self.provider = provider
        self._flag_handlers = flag_handlers
        self._started_at = time.monotonic()

    def add_step(self, step: RecutStep) -> None:
        self.trace.steps.append(step)
        self.trace.meta.total_steps = len(self.trace.steps)

    @property
    def risk_score(self) -> float:
        if not self.trace.steps:
            return 0.0
        return max(s.risk_score for s in self.trace.steps)

    def finalize(self) -> RecutTrace:
        elapsed = time.monotonic() - self._started_at
        self.trace.meta.duration_seconds = round(elapsed, 3)
        return self.trace


def trace(
    agent_id: str = "default",
    mode: TraceMode | str = TraceMode.PEEK,
    language: TraceLanguage | str = TraceLanguage.SIMPLE,
    provider: AbstractProvider | None = None,
    sample_rate: float = 1.0,
    trace_if: Callable[[RecutContext], bool] | None = None,
    flag_handlers: list[Callable] | None = None,
) -> Callable:
    """
    Decorator that wraps any async function and captures its agent run as a RecutTrace.

    Usage::

        @recut.trace(agent_id="my-agent", mode="peek")
        async def run_agent(prompt: str) -> str:
            ...

    The wrapped function receives a ``ctx: RecutContext`` keyword argument
    (injected automatically) so it can stream steps back to the tracer.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Selective tracing — honour sample_rate
            effective_rate = float(os.environ.get("RECUT_DEFAULT_SAMPLE_RATE", sample_rate))
            if random.random() > effective_rate:
                return await fn(*args, **kwargs)

            _mode = TraceMode(mode) if isinstance(mode, str) else mode
            _language = TraceLanguage(language) if isinstance(language, str) else language
            _provider = provider or _default_provider()

            trace_obj = RecutTrace(
                agent_id=agent_id,
                prompt=_extract_prompt(args, kwargs),
                mode=_mode,
                language=_language,
                meta=TraceMeta(
                    model=getattr(_provider, "model", "unknown"),
                    provider=_provider.__class__.__name__,
                ),
            )

            ctx = RecutContext(
                trace=trace_obj,
                provider=_provider,
                flag_handlers=flag_handlers or [],
            )

            # Honour trace_if predicate
            if trace_if is not None and not trace_if(ctx):
                return await fn(*args, **kwargs)

            kwargs["ctx"] = ctx
            result = await fn(*args, **kwargs)

            ctx.finalize()
            asyncio.create_task(_persist_trace(ctx.trace))

            return result

        return wrapper

    return decorator


@asynccontextmanager
async def trace_context(
    agent_id: str = "default",
    mode: TraceMode | str = TraceMode.PEEK,
    language: TraceLanguage | str = TraceLanguage.SIMPLE,
    provider: AbstractProvider | None = None,
) -> AsyncIterator[RecutContext]:
    """
    Async context manager alternative to the decorator.

    Usage::

        async with recut.trace_context(agent_id="my-agent") as ctx:
            async for step in provider.run_agent(prompt):
                ctx.add_step(step)
    """
    _mode = TraceMode(mode) if isinstance(mode, str) else mode
    _language = TraceLanguage(language) if isinstance(language, str) else language
    _provider = provider or _default_provider()

    trace_obj = RecutTrace(
        agent_id=agent_id,
        prompt="",
        mode=_mode,
        language=_language,
        meta=TraceMeta(
            model=getattr(_provider, "model", "unknown"),
            provider=_provider.__class__.__name__,
        ),
    )

    ctx = RecutContext(trace=trace_obj, provider=_provider, flag_handlers=[])
    try:
        yield ctx
    finally:
        ctx.finalize()
        asyncio.create_task(_persist_trace(ctx.trace))


def _extract_prompt(args: tuple, kwargs: dict) -> str:
    """Best-effort extraction of the prompt string from call arguments."""
    if "prompt" in kwargs:
        return str(kwargs["prompt"])
    if args:
        return str(args[0])
    return ""


def _default_provider() -> AbstractProvider:
    from recut.providers.anthropic import AnthropicProvider
    return AnthropicProvider()


async def _persist_trace(trace: RecutTrace) -> None:
    if is_open():
        return
    try:
        row = TraceRow(
            id=trace.id,
            created_at=trace.created_at,
            agent_id=trace.agent_id,
            prompt=trace.prompt,
            mode=trace.mode.value,
            language=trace.language.value,
            model=trace.meta.model,
            provider=trace.meta.provider,
            duration_seconds=trace.meta.duration_seconds,
            total_steps=trace.meta.total_steps,
            token_count=trace.meta.token_count,
            thinking_tokens=trace.meta.thinking_tokens,
            steps_json=json.dumps([s.model_dump(mode="json") for s in trace.steps]),
        )
        loop = asyncio.get_running_loop()
        client = StorageClient()
        await loop.run_in_executor(None, client.save_trace_row, row)
        record_success()
    except Exception:  # noqa: BLE001
        record_failure()
