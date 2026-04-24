from __future__ import annotations

from typing import Literal

from recut.flagging.engine import FlaggingEngine
from recut.plain.summariser import summarise_step, summarise_trace
from recut.schema.audit import AuditMode, AuditRecord, ReviewStatus, RiskProfile
from recut.schema.trace import FlagType, RecutFlag, RecutTrace, Severity, TraceMode


async def peek(
    trace: RecutTrace,
    flagging_depth: Literal["fast", "full"] = "fast",
) -> AuditRecord:
    """
    Fast triage mode. Defaults to layers 1-3 only (no LLM judge).
    Pass flagging_depth="full" to include the LLM judge on ambiguous steps.
    """
    engine = FlaggingEngine(mode=TraceMode.PEEK, flagging_depth=flagging_depth)
    await _score_trace_steps(trace, engine)
    return _build_audit_record(trace, AuditMode.PEEK)


async def audit(
    trace: RecutTrace,
    flagging_depth: Literal["fast", "full"] = "full",
) -> AuditRecord:
    """
    Full structured audit. Defaults to all four flagging layers.
    Pass flagging_depth="fast" to skip the LLM judge (cheaper, instant).
    Produces a complete AuditRecord suitable for compliance review.
    """
    engine = FlaggingEngine(mode=TraceMode.AUDIT, flagging_depth=flagging_depth)
    await _score_trace_steps(trace, engine)
    return _build_audit_record(trace, AuditMode.AUDIT)


async def _score_trace_steps(trace: RecutTrace, engine: FlaggingEngine) -> None:
    """Score all steps in the trace using the given engine, mutating in-place."""

    for i, step in enumerate(trace.steps):
        preceding = trace.steps[max(0, i - 2) : i]
        flags = await engine.score_step(step, preceding, trace.prompt)

        step.flags = flags
        step.risk_score = _compute_risk_score(flags)
        step.plain_summary = summarise_step(step, trace.language)


def _compute_risk_score(flags: list[RecutFlag]) -> float:
    if not flags:
        return 0.0
    weights = {Severity.LOW: 0.3, Severity.MEDIUM: 0.6, Severity.HIGH: 1.0}
    scores = [weights.get(f.severity, 0.0) for f in flags]
    return min(1.0, max(scores))


def _build_audit_record(trace: RecutTrace, mode: AuditMode) -> AuditRecord:
    all_flags = [f for step in trace.steps for f in step.flags]
    profile = _build_risk_profile(all_flags)

    highest: str | None = None
    if any(f.severity == Severity.HIGH for f in all_flags):
        highest = Severity.HIGH.value
    elif any(f.severity == Severity.MEDIUM for f in all_flags):
        highest = Severity.MEDIUM.value
    elif all_flags:
        highest = Severity.LOW.value

    return AuditRecord(
        trace_id=trace.id,
        mode=mode,
        behavioral_summary=summarise_trace(trace),
        flag_count=len(all_flags),
        highest_severity=highest,
        risk_profile=profile,
        review_status=ReviewStatus.PENDING_HUMAN
        if highest == Severity.HIGH.value
        else ReviewStatus.AUTO,
    )


def _build_risk_profile(flags: list[RecutFlag]) -> RiskProfile:
    profile = RiskProfile()
    counter_map = {
        FlagType.OVERCONFIDENCE: "overconfidence_count",
        FlagType.GOAL_DRIFT: "goal_drift_count",
        FlagType.SCOPE_CREEP: "scope_creep_count",
        FlagType.REASONING_GAP: "reasoning_gap_count",
        FlagType.UNCERTAINTY_SUPPRESSION: "uncertainty_suppression_count",
        FlagType.INSTRUCTION_DEVIATION: "instruction_deviation_count",
        FlagType.ANOMALOUS_TOOL_USE: "anomalous_tool_use_count",
        FlagType.REASONING_ACTION_MISMATCH: "reasoning_action_mismatch_count",
    }
    for f in flags:
        attr = counter_map.get(f.type)
        if attr:
            setattr(profile, attr, getattr(profile, attr) + 1)
    return profile
