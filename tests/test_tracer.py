"""
Tests for recut/core/tracer.py.
No live API calls — _persist_trace is mocked throughout.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from recut.core.tracer import (
    RecutContext,
    _extract_prompt,
    trace,
    trace_context,
)
from recut.schema.trace import (
    RecutStep,
    RecutTrace,
    StepType,
    TraceLanguage,
    TraceMeta,
    TraceMode,
)

# ---------------------------------------------------------------------------
# Minimal stub provider so we never hit AbstractProvider enforcement
# ---------------------------------------------------------------------------


class _StubProvider:
    model = "stub-model"

    async def capture_step(self, raw_response: dict) -> RecutStep:  # pragma: no cover
        raise NotImplementedError

    def supports_native_reasoning(self) -> bool:
        return False

    async def replay_from(
        self, steps, fork_index, injection
    ) -> list[RecutStep]:  # pragma: no cover
        raise NotImplementedError

    async def run_agent(self, prompt, system=None, tools=None):  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(index: int, risk_score: float = 0.0) -> RecutStep:
    return RecutStep(
        index=index,
        type=StepType.OUTPUT,
        content=f"Step {index}",
        risk_score=risk_score,
    )


# ===========================================================================
# trace_context — context manager
# ===========================================================================


class TestTraceContext:
    async def test_creates_recut_context_with_correct_agent_id(self):
        with (
            patch("recut.core.tracer._persist_trace", new=AsyncMock()),
            patch("recut.core.tracer._default_provider", return_value=_StubProvider()),
        ):
            async with trace_context(agent_id="my-agent", mode=TraceMode.PEEK) as ctx:
                assert ctx.trace.agent_id == "my-agent"

    async def test_creates_recut_context_with_correct_mode(self):
        with (
            patch("recut.core.tracer._persist_trace", new=AsyncMock()),
            patch("recut.core.tracer._default_provider", return_value=_StubProvider()),
        ):
            async with trace_context(agent_id="a", mode=TraceMode.AUDIT) as ctx:
                assert ctx.trace.mode == TraceMode.AUDIT

    async def test_creates_recut_context_with_correct_language(self):
        with (
            patch("recut.core.tracer._persist_trace", new=AsyncMock()),
            patch("recut.core.tracer._default_provider", return_value=_StubProvider()),
        ):
            async with trace_context(agent_id="a", language=TraceLanguage.POWER) as ctx:
                assert ctx.trace.language == TraceLanguage.POWER

    async def test_creates_recut_context_with_string_mode(self):
        with (
            patch("recut.core.tracer._persist_trace", new=AsyncMock()),
            patch("recut.core.tracer._default_provider", return_value=_StubProvider()),
        ):
            async with trace_context(agent_id="a", mode="audit") as ctx:
                assert ctx.trace.mode == TraceMode.AUDIT

    async def test_default_language_is_simple(self):
        with (
            patch("recut.core.tracer._persist_trace", new=AsyncMock()),
            patch("recut.core.tracer._default_provider", return_value=_StubProvider()),
        ):
            async with trace_context(agent_id="a") as ctx:
                assert ctx.trace.language == TraceLanguage.SIMPLE

    async def test_persist_trace_called_on_exit(self):
        mock_persist = AsyncMock()
        with (
            patch("recut.core.tracer._persist_trace", new=mock_persist),
            patch("recut.core.tracer._default_provider", return_value=_StubProvider()),
        ):
            async with trace_context(agent_id="a"):
                pass

        mock_persist.assert_called_once()

    async def test_explicit_provider_is_used(self):
        provider = _StubProvider()
        provider.model = "my-special-model"
        with patch("recut.core.tracer._persist_trace", new=AsyncMock()):
            async with trace_context(agent_id="a", provider=provider) as ctx:
                assert ctx.trace.meta.model == "my-special-model"
                assert ctx.provider is provider


# ===========================================================================
# RecutContext — add_step and risk_score
# ===========================================================================


class TestRecutContext:
    def _make_ctx(self) -> RecutContext:
        trace_obj = RecutTrace(
            agent_id="agent",
            prompt="test",
            mode=TraceMode.PEEK,
            meta=TraceMeta(model="m", provider="p"),
        )
        return RecutContext(trace=trace_obj, provider=_StubProvider(), flag_handlers=[])

    def test_add_step_increments_total_steps(self):
        ctx = self._make_ctx()
        assert ctx.trace.meta.total_steps == 0

        ctx.add_step(_make_step(0))
        assert ctx.trace.meta.total_steps == 1

        ctx.add_step(_make_step(1))
        assert ctx.trace.meta.total_steps == 2

    def test_add_step_appends_to_trace_steps(self):
        ctx = self._make_ctx()
        step = _make_step(0)
        ctx.add_step(step)
        assert ctx.trace.steps[-1] is step

    def test_risk_score_no_steps_returns_zero(self):
        ctx = self._make_ctx()
        assert ctx.risk_score == 0.0

    def test_risk_score_returns_max_across_steps(self):
        ctx = self._make_ctx()
        ctx.add_step(_make_step(0, risk_score=0.3))
        ctx.add_step(_make_step(1, risk_score=0.8))
        ctx.add_step(_make_step(2, risk_score=0.5))
        assert ctx.risk_score == 0.8

    def test_risk_score_single_step(self):
        ctx = self._make_ctx()
        ctx.add_step(_make_step(0, risk_score=0.65))
        assert ctx.risk_score == 0.65

    def test_finalize_sets_positive_duration(self):
        ctx = self._make_ctx()
        trace = ctx.finalize()
        assert trace.meta.duration_seconds is not None
        assert trace.meta.duration_seconds >= 0.0

    def test_finalize_returns_trace(self):
        ctx = self._make_ctx()
        result = ctx.finalize()
        assert result is ctx.trace

    def test_finalize_duration_increases_with_time(self):
        ctx = self._make_ctx()
        # Small delay to ensure duration is not zero on fast machines
        time.sleep(0.01)
        ctx.finalize()
        assert ctx.trace.meta.duration_seconds > 0.0


# ===========================================================================
# @trace decorator
# ===========================================================================


class TestTraceDecorator:
    async def test_sample_rate_zero_function_called_no_trace(self):
        """sample_rate=0.0 means function is never sampled — no trace created."""
        calls = []

        @trace(agent_id="a", sample_rate=0.0, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            calls.append(prompt)
            return "ok"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
            result = await my_agent("hello")

        assert result == "ok"
        assert calls == ["hello"]
        mock_persist.assert_not_called()

    async def test_sample_rate_one_trace_is_created(self):
        """sample_rate=1.0 means always trace — persist should be called."""

        @trace(agent_id="a", sample_rate=1.0, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            return "result"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
            await my_agent("test prompt")

        mock_persist.assert_called_once()
        saved_trace = mock_persist.call_args[0][0]
        assert isinstance(saved_trace, RecutTrace)
        assert saved_trace.agent_id == "a"

    async def test_trace_if_false_function_called_no_trace(self):
        """trace_if returning False → function runs but no trace is persisted."""
        calls = []

        @trace(agent_id="b", sample_rate=1.0, trace_if=lambda ctx: False, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            calls.append(prompt)
            return "done"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
            result = await my_agent("hi")

        assert result == "done"
        assert calls == ["hi"]
        mock_persist.assert_not_called()

    async def test_trace_if_true_trace_is_created(self):
        """trace_if returning True → trace is persisted."""

        @trace(agent_id="c", sample_rate=1.0, trace_if=lambda ctx: True, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            return "answer"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
            await my_agent("prompt text")

        mock_persist.assert_called_once()

    async def test_ctx_is_injected_as_keyword(self):
        """The wrapped function receives a ctx: RecutContext kwarg."""
        received_ctx = []

        @trace(agent_id="d", sample_rate=1.0, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            received_ctx.append(kwargs.get("ctx"))
            return "ok"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()):
            await my_agent("p")

        assert len(received_ctx) == 1
        assert received_ctx[0] is not None
        from recut.core.tracer import RecutContext

        assert isinstance(received_ctx[0], RecutContext)

    async def test_trace_captures_prompt_from_first_positional_arg(self):
        """The first positional arg is used as the prompt."""

        @trace(agent_id="e", sample_rate=1.0, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            return "ok"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
            await my_agent("tell me about Paris")

        saved_trace = mock_persist.call_args[0][0]
        assert saved_trace.prompt == "tell me about Paris"

    async def test_finalize_called_sets_duration(self):
        """After the decorator runs, the trace should have duration_seconds set."""

        @trace(agent_id="f", sample_rate=1.0, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            return "ok"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
            await my_agent("p")

        saved_trace = mock_persist.call_args[0][0]
        assert saved_trace.meta.duration_seconds is not None
        assert saved_trace.meta.duration_seconds >= 0.0


# ===========================================================================
# _extract_prompt
# ===========================================================================


class TestExtractPrompt:
    def test_positional_arg(self):
        assert _extract_prompt(("hello world",), {}) == "hello world"

    def test_keyword_arg(self):
        assert _extract_prompt((), {"prompt": "from kwargs"}) == "from kwargs"

    def test_keyword_arg_takes_precedence_over_positional(self):
        assert _extract_prompt(("positional",), {"prompt": "keyword"}) == "keyword"

    def test_no_args_returns_empty_string(self):
        assert _extract_prompt((), {}) == ""

    def test_non_string_positional_is_coerced(self):
        result = _extract_prompt((42,), {})
        assert result == "42"

    def test_non_string_keyword_is_coerced(self):
        result = _extract_prompt((), {"prompt": 999})
        assert result == "999"

    def test_empty_positional_tuple(self):
        assert _extract_prompt((), {"other_key": "value"}) == ""
