from __future__ import annotations

from recut.schema.trace import (
    RecutFlag,
    RecutStep,
    RecutTrace,
    Severity,
    StepType,
    TraceLanguage,
)


def summarise_step(step: RecutStep, language: TraceLanguage = TraceLanguage.SIMPLE) -> str:
    """Generate a plain-language summary for a single step."""
    if language == TraceLanguage.POWER:
        return _power_step_summary(step)
    return _simple_step_summary(step)


def summarise_trace(trace: RecutTrace) -> str:
    """Generate a behavioral summary for the full trace."""
    if not trace.steps:
        return "The agent completed the task with no recorded steps."

    flag_count = sum(len(s.flags) for s in trace.steps)
    high_flags = [f for s in trace.steps for f in s.flags if f.severity == Severity.HIGH]
    tool_calls = [s for s in trace.steps if s.type == StepType.TOOL_CALL]

    parts: list[str] = []

    if trace.language == TraceLanguage.SIMPLE:
        parts.append(f"The agent completed {trace.meta.total_steps} steps.")
        if tool_calls:
            parts.append(f"It used {len(tool_calls)} tool call{'s' if len(tool_calls) != 1 else ''}.")
        if flag_count == 0:
            parts.append("No behavioral issues were detected.")
        elif high_flags:
            parts.append(
                f"{flag_count} issue{'s were' if flag_count != 1 else ' was'} flagged, "
                f"including {len(high_flags)} high-severity concern{'s' if len(high_flags) != 1 else ''}."
            )
        else:
            parts.append(f"{flag_count} minor issue{'s were' if flag_count != 1 else ' was'} flagged.")
        if trace.meta.duration_seconds:
            parts.append(f"Run completed in {trace.meta.duration_seconds:.1f}s.")
    else:
        parts.append(
            f"Trace {trace.id} | agent={trace.agent_id} | model={trace.meta.model} | "
            f"steps={trace.meta.total_steps} | flags={flag_count} | "
            f"duration={trace.meta.duration_seconds:.3f}s"
        )

    return " ".join(parts)


def flag_suggested_action(flag: RecutFlag) -> str:
    """Return a suggested action string for a given flag."""
    from recut.schema.trace import FlagType
    if flag.severity == Severity.HIGH:
        return "escalate"
    if flag.type in (FlagType.REASONING_ACTION_MISMATCH, FlagType.GOAL_DRIFT):
        return "audit"
    if flag.type in (FlagType.ANOMALOUS_TOOL_USE, FlagType.SCOPE_CREEP):
        return "replay"
    return "peek"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _simple_step_summary(step: RecutStep) -> str:
    type_phrases = {
        StepType.REASONING: "The agent thought through the problem",
        StepType.TOOL_CALL: "The agent used a tool",
        StepType.TOOL_RESULT: "The tool returned a result",
        StepType.OUTPUT: "The agent produced an answer",
    }
    base = type_phrases.get(step.type, "The agent took an action")

    if step.reasoning:
        confidence_label = (
            "with high confidence"
            if step.reasoning.confidence >= 0.8
            else "with some uncertainty"
            if step.reasoning.confidence >= 0.4
            else "with low confidence"
        )
        base = f"{base} {confidence_label}"

    if step.flags:
        severities = {f.severity for f in step.flags}
        if Severity.HIGH in severities:
            base += " — flagged as high risk"
        elif Severity.MEDIUM in severities:
            base += " — flagged for review"
        else:
            base += " — minor flag"

    return base + "."


def _power_step_summary(step: RecutStep) -> str:
    parts = [
        f"[{step.index}] type={step.type.value}",
        f"risk={step.risk_score:.2f}",
        f"flags={len(step.flags)}",
    ]
    if step.reasoning:
        parts.append(
            f"reasoning={step.reasoning.source.value}(confidence={step.reasoning.confidence:.2f})"
        )
    return " | ".join(parts)
