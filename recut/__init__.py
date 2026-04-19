"""
recut-ai — Intercept, replay, and audit your AI agent runs.

Quick start::

    import recut

    @recut.trace(agent_id="my-agent", mode="peek")
    async def run_agent(prompt: str, ctx=None) -> str:
        async for step in ctx.provider.run_agent(prompt):
            ctx.add_step(step)
        return ctx.trace.steps[-1].content if ctx.trace.steps else ""

    # Or use the context manager form:
    async with recut.trace_context(agent_id="my-agent") as ctx:
        async for step in provider.run_agent(prompt):
            ctx.add_step(step)
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from recut.core.auditor import audit, peek
from recut.core.interceptor import InterceptSession, intercept
from recut.core.replayer import diff, replay
from recut.core.stress import stress
from recut.core.tracer import RecutContext, trace, trace_context
from recut.export.exporter import export, load_export
from recut.schema.hooks import FlagHandler, RecutFlagEvent
from recut.schema.trace import TraceLanguage, TraceMode

_flag_handlers: list[Callable] = []


def on_flag(fn: Callable) -> Callable:
    """Register a handler called whenever a step is flagged during intercept."""
    _flag_handlers.append(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


def get_flag_handlers() -> list[Callable]:
    """Return all registered flag handlers."""
    return list(_flag_handlers)


__all__ = [
    "trace",
    "trace_context",
    "RecutContext",
    "intercept",
    "InterceptSession",
    "replay",
    "diff",
    "peek",
    "audit",
    "stress",
    "export",
    "load_export",
    "on_flag",
    "get_flag_handlers",
    "RecutFlagEvent",
    "FlagHandler",
    "TraceMode",
    "TraceLanguage",
]

__version__ = "0.1.0"
