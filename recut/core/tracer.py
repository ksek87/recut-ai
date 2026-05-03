from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import random
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from recut.core.auditor import audit as _audit
from recut.core.auditor import peek as _peek
from recut.flagging.fingerprint import get_fingerprint_flags
from recut.providers.anthropic import AnthropicProvider
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

_log = logging.getLogger(__name__)


class RecutBudgetExceededError(Exception):
    """Raised when a trace accumulates token cost beyond its configured token_budget."""

    def __init__(self, agent_id: str, accumulated_cost: float, budget: float) -> None:
        self.agent_id = agent_id
        self.accumulated_cost = accumulated_cost
        self.budget = budget
        super().__init__(
            f"Agent '{agent_id}' exceeded token budget ({accumulated_cost:.4f} > {budget:.4f})"
        )


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
        token_budget: float | None = None,
        budget_hard_limit: bool = False,
    ):
        self.trace = trace
        self.provider = provider
        self._flag_handlers = flag_handlers
        self._started_at = time.monotonic()
        self._token_budget = token_budget
        self._budget_hard_limit = budget_hard_limit

    def add_step(self, step: RecutStep) -> None:
        self.trace.steps.append(step)
        self.trace.meta.total_steps = len(self.trace.steps)
        if self._token_budget is not None and step.token_cost is not None:
            accumulated = sum(s.token_cost for s in self.trace.steps if s.token_cost)
            if accumulated > self._token_budget:
                if self._budget_hard_limit:
                    raise RecutBudgetExceededError(
                        agent_id=self.trace.agent_id,
                        accumulated_cost=accumulated,
                        budget=self._token_budget,
                    )
                _log.warning(
                    "recut: agent '%s' cost %.4f exceeded budget %.4f",
                    self.trace.agent_id,
                    accumulated,
                    self._token_budget,
                )

    @property
    def risk_score(self) -> float:
        if not self.trace.steps:
            return 0.0
        return max(s.risk_score for s in self.trace.steps)

    def finalize(self) -> RecutTrace:
        elapsed = time.monotonic() - self._started_at
        self.trace.meta.duration_seconds = round(elapsed, 3)
        # Aggregate token counts and cost from steps
        token_total = sum(s.token_count for s in self.trace.steps if s.token_count)
        cost_total = sum(s.token_cost for s in self.trace.steps if s.token_cost)
        if token_total:
            self.trace.meta.token_count = token_total
        if cost_total:
            self.trace.meta.token_cost = round(cost_total, 6)
        return self.trace


def trace(
    agent_id: str = "default",
    mode: TraceMode | str = TraceMode.PEEK,
    language: TraceLanguage | str = TraceLanguage.SIMPLE,
    provider: AbstractProvider | None = None,
    sample_rate: float = 1.0,
    trace_if: Callable[[RecutContext], bool] | None = None,
    flag_handlers: list[Callable] | None = None,
    flagging_depth: Literal["fast", "full"] | None = None,
    token_budget: float | None = None,
    budget_hard_limit: bool = False,
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
            try:
                effective_rate = float(os.environ.get("RECUT_DEFAULT_SAMPLE_RATE", sample_rate))
            except (ValueError, TypeError):
                _log.warning(
                    "recut: invalid RECUT_DEFAULT_SAMPLE_RATE env var; using default %s",
                    sample_rate,
                )
                effective_rate = float(sample_rate)
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
                token_budget=token_budget,
                budget_hard_limit=budget_hard_limit,
            )

            # trace_if exceptions are caught and treated as False (skip tracing)
            if trace_if is not None:
                try:
                    if not trace_if(ctx):
                        return await fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("recut: trace_if predicate raised %r; skipping trace", exc)
                    return await fn(*args, **kwargs)

            kwargs["ctx"] = ctx
            result = await fn(*args, **kwargs)

            ctx.finalize()

            if flagging_depth is not None:
                if flagging_depth == "full":
                    await _audit(ctx.trace, flagging_depth="full")
                else:
                    await _peek(ctx.trace, flagging_depth="fast")

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
    return AnthropicProvider()


async def _maybe_fingerprint(trace: RecutTrace) -> None:
    """Load historical traces for this agent and attach fingerprint flags to the last step."""
    try:
        client = StorageClient()
        loop = asyncio.get_running_loop()
        history = await loop.run_in_executor(None, client.load_recent_traces, trace.agent_id, 50)
        flags = get_fingerprint_flags(trace, history)
        if flags and trace.steps:
            trace.steps[-1].flags.extend(flags)
    except Exception as exc:
        _log.debug("recut: fingerprinting skipped: %s", exc)


async def _persist_trace(trace: RecutTrace) -> None:
    if is_open():
        return
    try:
        await _maybe_fingerprint(trace)
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
