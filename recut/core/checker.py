"""
recut check — behavioral regression gate for CI.

Compares the most recent trace for an agent against a stored baseline and
fails (non-zero exit in the CLI) when flag rate, severity, cost, or
step-repetition regress beyond configured thresholds.
"""

from __future__ import annotations

from recut.core.auditor import peek
from recut.schema.check import CheckOutcome, CheckReport
from recut.schema.trace import RecutTrace, Severity
from recut.storage.db import StorageClient
from recut.utils import parse_float_env


class CheckError(Exception):
    """Raised when a check cannot run at all (e.g. no traces for the agent)."""


async def check_agent(
    agent_id: str,
    baseline_id: str | None = None,
    client: StorageClient | None = None,
) -> CheckReport:
    """
    Run the regression gate for an agent's most recent trace.

    Baseline resolution order:
    1. Explicit ``baseline_id``
    2. The baseline previously stored for this agent
    3. None — the current trace is stored as the new baseline and the
       report passes (first run)
    """
    client = client or StorageClient()

    traces = client.load_recent_traces(agent_id, limit=1)
    if not traces:
        raise CheckError(f"No traces found for agent '{agent_id}'")
    target = traces[0]
    await _ensure_flags(target)

    baseline: RecutTrace | None = None
    if baseline_id:
        baseline = client.load_trace(baseline_id)
        if baseline is None:
            raise CheckError(f"Baseline trace not found: {baseline_id}")
    else:
        row = client.get_baseline(agent_id)
        if row:
            baseline = client.load_trace(row.trace_id)

    if baseline is None:
        client.save_baseline(agent_id, target.id)
        return CheckReport(
            agent_id=agent_id,
            trace_id=target.id,
            passed=True,
            first_run=True,
        )

    await _ensure_flags(baseline)
    checks = run_checks(target, baseline)
    return CheckReport(
        agent_id=agent_id,
        trace_id=target.id,
        baseline_trace_id=baseline.id,
        passed=all(c.passed for c in checks),
        checks=checks,
    )


async def _ensure_flags(trace: RecutTrace) -> None:
    """Score the trace with the fast (free) flagging layers if it has no flags yet."""
    if trace.steps and not any(s.flags for s in trace.steps):
        await peek(trace, flagging_depth="fast")


def run_checks(target: RecutTrace, baseline: RecutTrace) -> list[CheckOutcome]:
    """Pure comparison of a target trace against a baseline trace."""
    return [
        _check_flag_rate(target),
        _check_high_severity(target, baseline),
        _check_cost_delta(target, baseline),
        _check_repetition(target),
    ]


def _check_flag_rate(target: RecutTrace) -> CheckOutcome:
    threshold = parse_float_env("RECUT_CHECK_MAX_FLAG_RATE", 0.15)
    total = max(len(target.steps), 1)
    flagged = sum(1 for s in target.steps if s.flags)
    rate = flagged / total
    return CheckOutcome(
        name="flag_rate",
        passed=rate <= threshold,
        value=round(rate, 4),
        threshold=threshold,
        detail=f"{flagged}/{total} steps flagged",
    )


def _check_high_severity(target: RecutTrace, baseline: RecutTrace) -> CheckOutcome:
    target_high = _high_flag_count(target)
    baseline_high = _high_flag_count(baseline)
    passed = target_high == 0 or baseline_high > 0
    return CheckOutcome(
        name="high_severity",
        passed=passed,
        value=float(target_high),
        threshold=float(baseline_high),
        detail=f"{target_high} high-severity flags (baseline: {baseline_high})",
    )


def _check_cost_delta(target: RecutTrace, baseline: RecutTrace) -> CheckOutcome:
    max_delta = parse_float_env("RECUT_CHECK_MAX_COST_DELTA", 0.25)
    target_cost = _trace_cost(target)
    baseline_cost = _trace_cost(baseline)
    if baseline_cost <= 0:
        return CheckOutcome(
            name="cost_delta",
            passed=True,
            value=0.0,
            threshold=max_delta,
            detail="baseline has no cost data — skipped",
        )
    delta = (target_cost - baseline_cost) / baseline_cost
    return CheckOutcome(
        name="cost_delta",
        passed=delta <= max_delta,
        value=round(delta, 4),
        threshold=max_delta,
        detail=f"cost {target_cost:.4f} vs baseline {baseline_cost:.4f} ({delta:+.0%})",
    )


def _check_repetition(target: RecutTrace) -> CheckOutcome:
    threshold = parse_float_env("RECUT_CHECK_MAX_REPETITION", 0.5)
    total = max(len(target.steps), 1)
    unique = len({s.content for s in target.steps})
    ratio = 1.0 - unique / total
    return CheckOutcome(
        name="repetition",
        passed=ratio <= threshold,
        value=round(ratio, 4),
        threshold=threshold,
        detail=f"{unique} unique contents across {total} steps",
    )


def _high_flag_count(trace: RecutTrace) -> int:
    return sum(1 for s in trace.steps for f in s.flags if f.severity == Severity.HIGH)


def _trace_cost(trace: RecutTrace) -> float:
    return sum(s.token_cost or 0.0 for s in trace.steps)
