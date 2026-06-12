"""Tests for recut check — the CI regression gate (recut/core/checker.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from recut.core.checker import CheckError, check_agent, run_checks
from recut.schema.trace import (
    FlagSource,
    FlagType,
    RecutFlag,
    RecutStep,
    RecutTrace,
    Severity,
    StepType,
    TraceMeta,
    TraceMode,
)


def _step(i: int, content: str, severity: Severity | None = None, cost: float = 0.01) -> RecutStep:
    step = RecutStep(index=i, type=StepType.OUTPUT, content=content, token_cost=cost)
    if severity is not None:
        step.flags = [
            RecutFlag(
                type=FlagType.OVERCONFIDENCE,
                severity=severity,
                plain_reason="test",
                step_id=step.id,
                source=FlagSource.RULE,
            )
        ]
    return step


def _trace(steps: list[RecutStep], agent_id: str = "agent-x") -> RecutTrace:
    return RecutTrace(
        agent_id=agent_id,
        prompt="do the thing",
        mode=TraceMode.PEEK,
        meta=TraceMeta(model="m", provider="anthropic", total_steps=len(steps)),
        steps=steps,
    )


def _clean_trace(n: int = 10) -> RecutTrace:
    return _trace([_step(i, f"unique content {i}") for i in range(n)])


class TestRunChecks:
    def test_all_pass_on_identical_traces(self):
        target, baseline = _clean_trace(), _clean_trace()
        checks = run_checks(target, baseline)
        assert all(c.passed for c in checks)
        assert {c.name for c in checks} == {
            "flag_rate",
            "high_severity",
            "cost_delta",
            "repetition",
        }

    def test_flag_rate_regression_fails(self):
        steps = [_step(i, f"c{i}", severity=Severity.MEDIUM if i < 5 else None) for i in range(10)]
        checks = {c.name: c for c in run_checks(_trace(steps), _clean_trace())}
        assert not checks["flag_rate"].passed
        assert checks["flag_rate"].value == 0.5

    def test_new_high_severity_fails(self):
        steps = [_step(0, "c0", severity=Severity.HIGH)] + [_step(i, f"c{i}") for i in range(1, 10)]
        checks = {c.name: c for c in run_checks(_trace(steps), _clean_trace())}
        assert not checks["high_severity"].passed

    def test_high_severity_passes_when_baseline_also_has_high(self):
        target = _trace([_step(0, "a", severity=Severity.HIGH)])
        baseline = _trace([_step(0, "b", severity=Severity.HIGH)])
        checks = {c.name: c for c in run_checks(target, baseline)}
        assert checks["high_severity"].passed

    def test_cost_spike_fails(self):
        target = _trace([_step(i, f"c{i}", cost=0.10) for i in range(10)])
        baseline = _trace([_step(i, f"c{i}", cost=0.01) for i in range(10)])
        checks = {c.name: c for c in run_checks(target, baseline)}
        assert not checks["cost_delta"].passed

    def test_cost_check_skipped_without_baseline_cost(self):
        target = _trace([_step(0, "a", cost=5.0)])
        baseline = _trace([_step(0, "b", cost=0.0)])
        checks = {c.name: c for c in run_checks(target, baseline)}
        assert checks["cost_delta"].passed
        assert "skipped" in checks["cost_delta"].detail

    def test_loop_repetition_fails(self):
        steps = [_step(i, "same tool call over and over") for i in range(10)]
        checks = {c.name: c for c in run_checks(_trace(steps), _clean_trace())}
        assert not checks["repetition"].passed


class TestCheckAgent:
    def _client_with(self, target, baseline_row=None, baseline_trace=None):
        client = MagicMock()
        client.load_recent_traces.return_value = [target]
        client.get_baseline.return_value = baseline_row
        client.load_trace.return_value = baseline_trace
        return client

    async def test_no_traces_raises(self):
        client = MagicMock()
        client.load_recent_traces.return_value = []
        with pytest.raises(CheckError):
            await check_agent("agent-x", client=client)

    async def test_first_run_stores_baseline_and_passes(self):
        target = _trace([_step(0, "a", severity=Severity.LOW)])
        client = self._client_with(target)
        report = await check_agent("agent-x", client=client)
        assert report.first_run
        assert report.passed
        client.save_baseline.assert_called_once_with("agent-x", target.id)

    async def test_compares_against_stored_baseline(self):
        target = _trace([_step(0, "a", severity=Severity.LOW)])
        baseline = _trace([_step(0, "b", severity=Severity.LOW)])
        row = MagicMock(trace_id=baseline.id)
        client = self._client_with(target, baseline_row=row, baseline_trace=baseline)
        report = await check_agent("agent-x", client=client)
        assert not report.first_run
        assert report.baseline_trace_id == baseline.id
        assert len(report.checks) == 4

    async def test_explicit_baseline_not_found_raises(self):
        target = _trace([_step(0, "a", severity=Severity.LOW)])
        client = self._client_with(target, baseline_trace=None)
        with pytest.raises(CheckError):
            await check_agent("agent-x", baseline_id="missing", client=client)
