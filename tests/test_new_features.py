"""
Tests for the three PM-priority features:
  1. flagging_depth on @recut.trace()
  2. Budget guardrails (token_budget + budget_hard_limit)
  3. Behavioral fingerprinting
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recut.core.tracer import RecutBudgetExceededError, RecutContext, trace
from recut.flagging.fingerprint import _mean, _stddev, _zscore, get_fingerprint_flags
from recut.schema.trace import (
    FlagSource,
    FlagType,
    RecutStep,
    RecutTrace,
    Severity,
    StepType,
    TraceMeta,
    TraceMode,
)
from tests._helpers import _StubProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trace(agent_id: str = "agent", steps: int = 3, risk: float = 0.0) -> RecutTrace:
    t = RecutTrace(
        agent_id=agent_id,
        prompt="hello",
        mode=TraceMode.PEEK,
        meta=TraceMeta(model="m", provider="p"),
    )
    for i in range(steps):
        s = RecutStep(index=i, type=StepType.OUTPUT, content=f"step {i}", risk_score=risk)
        t.steps.append(s)
    return t


def _varied_history(n: int, base_steps: int = 3) -> list[RecutTrace]:
    """History with slight natural variance so stddev > 0."""
    traces = []
    for i in range(n):
        # Alternate between base_steps-1 and base_steps+1 to create variance
        count = base_steps - 1 if i % 2 == 0 else base_steps + 1
        traces.append(_make_trace(steps=count))
    return traces


# ===========================================================================
# 1. flagging_depth on @recut.trace()
# ===========================================================================


class TestFlaggingDepthDecorator:
    async def test_flagging_depth_fast_calls_peek(self):
        """flagging_depth='fast' calls peek() inline after the agent runs."""
        peek_calls = []

        async def fake_peek(tr, flagging_depth="fast"):
            peek_calls.append(flagging_depth)

        @trace(agent_id="a", sample_rate=1.0, provider=_StubProvider(), flagging_depth="fast")
        async def my_agent(prompt: str, **kwargs) -> str:
            return "ok"

        with (
            patch("recut.core.tracer._persist_trace", new=AsyncMock()),
            patch("recut.core.tracer._peek", fake_peek),
        ):
            await my_agent("hello")

        assert peek_calls == ["fast"]

    async def test_flagging_depth_full_calls_audit(self):
        """flagging_depth='full' calls audit() inline."""
        audit_calls = []

        async def fake_audit(tr, flagging_depth="full"):
            audit_calls.append(flagging_depth)

        @trace(agent_id="a", sample_rate=1.0, provider=_StubProvider(), flagging_depth="full")
        async def my_agent(prompt: str, **kwargs) -> str:
            return "ok"

        with (
            patch("recut.core.tracer._persist_trace", new=AsyncMock()),
            patch("recut.core.tracer._audit", fake_audit),
        ):
            await my_agent("hello")

        assert audit_calls == ["full"]

    async def test_flagging_depth_none_does_not_call_scoring(self):
        """flagging_depth=None (default) does not call peek or audit inline."""
        with (
            patch("recut.core.tracer._persist_trace", new=AsyncMock()),
            patch("recut.core.tracer._peek", new=AsyncMock()) as mock_peek,
            patch("recut.core.tracer._audit", new=AsyncMock()) as mock_audit,
        ):

            @trace(agent_id="a", sample_rate=1.0, provider=_StubProvider())
            async def my_agent(prompt: str, **kwargs) -> str:
                return "ok"

            await my_agent("hello")

        mock_peek.assert_not_called()
        mock_audit.assert_not_called()

    async def test_flagging_depth_scoring_happens_before_persist(self):
        """peek runs before _persist_trace is scheduled — both are called once."""
        call_order = []

        async def fake_peek(tr, flagging_depth="fast"):
            call_order.append("peek")

        import asyncio as _asyncio

        original_create_task = _asyncio.create_task

        def capture_task(coro, **kw):
            call_order.append("create_task")
            return original_create_task(coro, **kw)

        @trace(agent_id="a", sample_rate=1.0, provider=_StubProvider(), flagging_depth="fast")
        async def my_agent(prompt: str, **kwargs) -> str:
            return "ok"

        with (
            patch("recut.core.tracer._peek", fake_peek),
            patch("recut.core.tracer.asyncio.create_task", side_effect=capture_task),
            patch("recut.core.tracer.StorageClient"),
        ):
            await my_agent("hello")

        assert call_order == ["peek", "create_task"]


# ===========================================================================
# 2. Budget guardrails
# ===========================================================================


class TestBudgetGuardrails:
    def _make_ctx(
        self,
        token_budget: float | None,
        budget_hard_limit: bool = False,
    ) -> RecutContext:
        trace_obj = _make_trace()
        trace_obj.steps.clear()
        return RecutContext(
            trace=trace_obj,
            provider=_StubProvider(),
            flag_handlers=[],
            token_budget=token_budget,
            budget_hard_limit=budget_hard_limit,
        )

    def _make_step(self, cost: float) -> RecutStep:
        return RecutStep(index=0, type=StepType.OUTPUT, content="x", token_cost=cost)

    def test_hard_limit_raises_when_exceeded(self):
        """add_step raises RecutBudgetExceededError when cost exceeds budget."""
        ctx = self._make_ctx(token_budget=0.01, budget_hard_limit=True)
        ctx.add_step(self._make_step(0.005))
        with pytest.raises(RecutBudgetExceededError) as exc_info:
            ctx.add_step(self._make_step(0.01))
        assert exc_info.value.accumulated_cost > exc_info.value.budget
        assert exc_info.value.agent_id == "agent"

    def test_soft_limit_logs_warning_and_does_not_raise(self, caplog):
        """add_step with budget_hard_limit=False logs but does not raise."""
        import logging

        ctx = self._make_ctx(token_budget=0.001, budget_hard_limit=False)
        with caplog.at_level(logging.WARNING, logger="recut.core.tracer"):
            ctx.add_step(self._make_step(0.005))  # exceeds 0.001 → warning only
        assert any("exceeded budget" in r.message for r in caplog.records)

    def test_no_budget_no_check(self):
        """Steps are added normally when token_budget is None."""
        ctx = self._make_ctx(token_budget=None)
        for _ in range(10):
            ctx.add_step(self._make_step(1.0))
        assert len(ctx.trace.steps) == 10

    def test_step_without_cost_skips_budget_check(self):
        """Steps with token_cost=None do not trigger the budget guard."""
        ctx = self._make_ctx(token_budget=0.001, budget_hard_limit=True)
        step = RecutStep(index=0, type=StepType.OUTPUT, content="x", token_cost=None)
        ctx.add_step(step)  # must not raise

    def test_budget_exceeded_error_attributes(self):
        err = RecutBudgetExceededError(agent_id="my-agent", accumulated_cost=0.15, budget=0.10)
        assert err.agent_id == "my-agent"
        assert err.accumulated_cost == pytest.approx(0.15)
        assert err.budget == pytest.approx(0.10)
        assert "my-agent" in str(err)

    async def test_decorator_propagates_budget_to_context(self):
        """token_budget and budget_hard_limit flow from decorator into RecutContext."""
        captured_ctx: list[RecutContext] = []

        @trace(
            agent_id="a",
            sample_rate=1.0,
            provider=_StubProvider(),
            token_budget=0.05,
            budget_hard_limit=True,
        )
        async def my_agent(prompt: str, ctx: RecutContext | None = None, **kwargs) -> str:
            captured_ctx.append(ctx)
            return "ok"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()):
            await my_agent("hi")

        assert captured_ctx[0]._token_budget == pytest.approx(0.05)
        assert captured_ctx[0]._budget_hard_limit is True

    def test_budget_not_exceeded_below_threshold(self):
        """Steps that stay under budget do not trigger the guard."""
        ctx = self._make_ctx(token_budget=1.0, budget_hard_limit=True)
        ctx.add_step(self._make_step(0.3))
        ctx.add_step(self._make_step(0.3))
        # 0.6 < 1.0 — no exception, 2 steps present
        assert len(ctx.trace.steps) == 2


# ===========================================================================
# 3. Behavioral fingerprinting
# ===========================================================================


class TestFingerprintMath:
    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_mean_values(self):
        assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_stddev_single_returns_zero(self):
        assert _stddev([5.0]) == 0.0

    def test_stddev_uniform_returns_zero(self):
        assert _stddev([3.0, 3.0, 3.0]) == pytest.approx(0.0)

    def test_stddev_sample_formula(self):
        # [1, 3] → mean=2, sample var=(1+1)/1=2, stddev=√2
        assert _stddev([1.0, 3.0]) == pytest.approx(2.0**0.5)

    def test_zscore_no_variance_returns_none(self):
        assert _zscore(5.0, [5.0, 5.0, 5.0]) is None

    def test_zscore_single_element_returns_none(self):
        assert _zscore(5.0, [3.0]) is None

    def test_zscore_positive(self):
        # population [1, 3]: mean=2, sample stddev=√2; z(4) = (4-2)/√2 = √2 ≈ 1.414
        z = _zscore(4.0, [1.0, 3.0])
        assert z == pytest.approx(2.0**0.5, rel=1e-4)

    def test_zscore_negative(self):
        z = _zscore(0.0, [1.0, 3.0])
        assert z is not None
        assert z < 0


class TestGetFingerprintFlags:
    def test_too_little_history_returns_empty(self):
        trace = _make_trace(steps=3)
        history = _varied_history(4)  # _MIN_HISTORY=5
        assert get_fingerprint_flags(trace, history) == []

    def test_no_anomaly_returns_empty(self):
        # History alternates 2/4 steps → baseline ~3, stddev ~1.15
        # Current trace: 3 steps → z ≈ 0, no flag
        history = _varied_history(10, base_steps=3)
        trace = _make_trace(steps=3)
        flags = get_fingerprint_flags(trace, history)
        assert flags == []

    def test_step_count_spike_flagged(self):
        # History alternates 2/4 steps (mean≈3, sddev≈1.15)
        # 50 steps → z ≈ (50-3)/1.15 ≈ 41σ — must be flagged
        history = _varied_history(10, base_steps=3)
        trace = _make_trace(steps=50)
        flags = get_fingerprint_flags(trace, history)
        assert any(f.source == FlagSource.FINGERPRINT for f in flags)
        assert any(f.type == FlagType.SCOPE_CREEP for f in flags)

    def test_risk_spike_flagged(self):
        # History has low risk, current trace has extreme risk
        history = [_make_trace(steps=3, risk=0.1 if i % 2 == 0 else 0.2) for i in range(10)]
        trace = _make_trace(steps=3, risk=0.95)
        flags = get_fingerprint_flags(trace, history)
        assert any(f.source == FlagSource.FINGERPRINT for f in flags)
        assert any(f.type == FlagType.OVERCONFIDENCE for f in flags)

    def test_high_severity_extreme_spike(self):
        # z ≥ 3.5 → HIGH severity
        history = _varied_history(10, base_steps=3)
        trace = _make_trace(steps=200)  # extreme
        flags = get_fingerprint_flags(trace, history)
        assert any(f.severity == Severity.HIGH for f in flags)

    def test_empty_trace_returns_empty(self):
        t = _make_trace(steps=0)
        assert get_fingerprint_flags(t, _varied_history(10)) == []

    def test_flag_step_id_is_last_step(self):
        history = _varied_history(10, base_steps=3)
        trace = _make_trace(steps=50)
        flags = get_fingerprint_flags(trace, history)
        assert flags
        assert all(f.step_id == trace.steps[-1].id for f in flags)

    def test_flag_source_is_fingerprint(self):
        history = _varied_history(10, base_steps=3)
        trace = _make_trace(steps=50)
        flags = get_fingerprint_flags(trace, history)
        assert all(f.source == FlagSource.FINGERPRINT for f in flags)


class TestFingerprintIntegration:
    async def test_fingerprint_flags_attached_before_persist(self):
        """_maybe_fingerprint attaches flags to the last step before save."""
        from recut.core.tracer import _maybe_fingerprint

        trace_obj = _make_trace(steps=50)  # spike vs varied history
        history = _varied_history(10, base_steps=3)

        mock_client = MagicMock()
        mock_client.load_recent_traces.return_value = history

        with patch("recut.core.tracer.StorageClient", return_value=mock_client):
            await _maybe_fingerprint(trace_obj)

        last_step = trace_obj.steps[-1]
        assert any(f.source == FlagSource.FINGERPRINT for f in last_step.flags)

    async def test_fingerprint_failure_does_not_crash(self):
        """If fingerprinting raises, _maybe_fingerprint silently skips."""
        from recut.core.tracer import _maybe_fingerprint

        trace_obj = _make_trace(steps=3)

        with patch("recut.core.tracer.StorageClient", side_effect=RuntimeError("db down")):
            await _maybe_fingerprint(trace_obj)  # must not raise

        assert all(f.source != FlagSource.FINGERPRINT for s in trace_obj.steps for f in s.flags)

    async def test_fingerprint_not_enough_history_leaves_trace_clean(self):
        """With fewer than _MIN_HISTORY traces, no fingerprint flags are added."""
        from recut.core.tracer import _maybe_fingerprint

        trace_obj = _make_trace(steps=50)
        history = _varied_history(3, base_steps=3)  # below min_history

        mock_client = MagicMock()
        mock_client.load_recent_traces.return_value = history

        with patch("recut.core.tracer.StorageClient", return_value=mock_client):
            await _maybe_fingerprint(trace_obj)

        fingerprint_flags = [
            f for s in trace_obj.steps for f in s.flags if f.source == FlagSource.FINGERPRINT
        ]
        assert fingerprint_flags == []
