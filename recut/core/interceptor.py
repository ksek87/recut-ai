from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable, Optional

from recut.flagging.engine import FlaggingEngine
from recut.plain.summariser import flag_suggested_action, summarise_step
from recut.schema.hooks import FlagHandler, RecutFlagEvent
from recut.schema.trace import RecutFlag, RecutStep, RecutTrace, TraceMode


class InterceptSession:
    """
    Live intercept session. Wraps an async step generator and applies
    layer 1 + layer 3 flagging in real time as steps stream in.

    When a flag fires at or above pause_on_severity, the session pauses
    and fires all registered flag handlers before continuing.
    """

    def __init__(
        self,
        trace: RecutTrace,
        flag_handlers: list[FlagHandler],
        pause_on_severity: Optional[str] = None,
    ):
        self.trace = trace
        self._flag_handlers = flag_handlers
        self._pause_on_severity = pause_on_severity
        self._engine = FlaggingEngine(mode=TraceMode.INTERCEPT)
        self._paused = asyncio.Event()
        self._paused.set()  # not paused initially

    async def process_step(self, step: RecutStep) -> RecutStep:
        """Score a step and fire handlers, optionally pausing the session."""
        preceding = self.trace.steps[-2:] if self.trace.steps else []
        flags = await self._engine.score_step(step, preceding, self.trace.prompt)

        step.flags = flags
        step.plain_summary = summarise_step(step, self.trace.language)
        self.trace.steps.append(step)

        for flag in flags:
            event = RecutFlagEvent(
                trace_id=self.trace.id,
                step_id=step.id,
                flag=flag,
                suggested_action=flag_suggested_action(flag),
                preceding_steps=list(preceding),
                agent_id=self.trace.agent_id,
            )
            for handler in self._flag_handlers:
                try:
                    result = handler(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

            if self._should_pause(flag):
                self._paused.clear()
                await self._paused.wait()

        return step

    def resume(self) -> None:
        """Resume a paused intercept session."""
        self._paused.set()

    def _should_pause(self, flag: RecutFlag) -> bool:
        if self._pause_on_severity is None:
            return False
        severity_order = {"low": 1, "medium": 2, "high": 3}
        flag_level = severity_order.get(flag.severity.value, 0)
        pause_level = severity_order.get(self._pause_on_severity, 999)
        return flag_level >= pause_level


async def intercept(
    trace: RecutTrace,
    step_generator: AsyncIterator[RecutStep],
    flag_handlers: list[FlagHandler] | None = None,
    pause_on_severity: Optional[str] = None,
) -> AsyncIterator[RecutStep]:
    """
    Wrap a step generator with real-time interception.

    Usage::

        async for step in recut.intercept(trace, provider.run_agent(prompt)):
            print(step.plain_summary)
    """
    session = InterceptSession(
        trace=trace,
        flag_handlers=flag_handlers or [],
        pause_on_severity=pause_on_severity,
    )

    async for raw_step in step_generator:
        yield await session.process_step(raw_step)
