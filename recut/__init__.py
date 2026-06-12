"""
recut-ai — Regression testing, replay, and counterfactual debugging for AI agents.

Zero-change instrumentation::

    import recut
    recut.init(agent_id="my-service")  # patches anthropic + openai SDKs

    with recut.run() as run_id:
        # Your existing agent code — completely unchanged.
        # All LLM calls inside this block are grouped into one trace.
        ...

Decorator form (full control)::

    @recut.trace(agent_id="my-agent", mode="peek")
    async def run_agent(prompt: str, ctx=None) -> str:
        async for step in ctx.provider.run_agent(prompt):
            ctx.add_step(step)
        return ctx.trace.steps[-1].content if ctx.trace.steps else ""
"""

from __future__ import annotations

from collections.abc import Callable

from recut import hooks as _hooks
from recut.auto import init, run
from recut.core.auditor import audit, peek
from recut.core.interceptor import InterceptSession, intercept
from recut.core.replayer import diff, replay
from recut.core.stress import stress
from recut.core.tracer import RecutBudgetExceededError, RecutContext, trace, trace_context
from recut.export.exporter import export, load_export
from recut.schema.hooks import FlagHandler, RecutFlagEvent
from recut.schema.trace import TraceLanguage, TraceMode


def on_flag(
    fn: Callable | None = None,
    *,
    severity: str | None = None,
    flag_type: str | None = None,
) -> Callable:
    """Register a global flag handler fired in all modes (peek, audit, intercept).

    Usage::

        @recut.on_flag
        def handle(event): ...

        @recut.on_flag(severity="high", flag_type="overconfidence")
        async def handle_high(event): ...
    """

    def decorator(func: Callable) -> Callable:
        _hooks.register(func, severity=severity, flag_type=flag_type)
        return func

    if fn is not None:
        return decorator(fn)
    return decorator


def get_flag_handlers() -> list[Callable]:
    """Return all registered global flag handler callables."""
    return [h for h, _ in _hooks.get_all()]


__all__ = [
    "init",
    "run",
    "trace",
    "trace_context",
    "RecutContext",
    "RecutBudgetExceededError",
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

__version__ = "0.5.0"
