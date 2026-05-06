"""Layer 1: deterministic rule-based flagging — no model, no API, zero cost."""

from __future__ import annotations

from recut.schema.trace import FlagSource, FlagType, RecutFlag, RecutStep, Severity, StepType
from recut.utils import parse_int_env


def layer1_rules(step: RecutStep, preceding: list[RecutStep]) -> list[RecutFlag]:
    flags: list[RecutFlag] = []

    if (
        step.type in (StepType.TOOL_CALL, StepType.OUTPUT)
        and step.reasoning
        and not step.reasoning.content.strip()
    ):
        flags.append(
            RecutFlag(
                type=FlagType.REASONING_GAP,
                severity=Severity.MEDIUM,
                plain_reason="The agent took an action without any reasoning — it's unclear why it made this choice.",
                step_id=step.id,
                source=FlagSource.RULE,
            )
        )

    if (
        step.type == StepType.TOOL_CALL
        and step.reasoning is None
        and not any(p.type == StepType.REASONING for p in preceding)
    ):
        flags.append(
            RecutFlag(
                type=FlagType.ANOMALOUS_TOOL_USE,
                severity=Severity.LOW,
                plain_reason="The agent used a tool without any visible reasoning beforehand — worth a quick look.",
                step_id=step.id,
                source=FlagSource.RULE,
            )
        )

    if step.type == StepType.TOOL_CALL and preceding:
        identical = [
            p for p in preceding if p.type == StepType.TOOL_CALL and p.content == step.content
        ]
        if identical:
            flags.append(
                RecutFlag(
                    type=FlagType.ANOMALOUS_TOOL_USE,
                    severity=Severity.HIGH,
                    plain_reason="The agent called the same tool with identical inputs more than once — this looks like a loop.",
                    step_id=step.id,
                    source=FlagSource.RULE,
                )
            )

    scope_creep_threshold = parse_int_env("RECUT_SCOPE_CREEP_THRESHOLD", 20, minimum=1)
    if step.index > scope_creep_threshold:
        flags.append(
            RecutFlag(
                type=FlagType.SCOPE_CREEP,
                severity=Severity.LOW,
                plain_reason=f"The agent is on step {step.index + 1}, which is more steps than expected for most tasks.",
                step_id=step.id,
                source=FlagSource.RULE,
            )
        )

    return flags
