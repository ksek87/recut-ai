"""Layer 3: native reasoning/action mismatch detection (Claude extended thinking only)."""

from __future__ import annotations

from recut.flagging.flags import CONFIDENCE_PHRASES, UNCERTAINTY_PHRASES
from recut.schema.trace import FlagSource, FlagType, ReasoningSource, RecutFlag, RecutStep, Severity


def layer3_native_mismatch(step: RecutStep) -> RecutFlag | None:
    """Return a flag when the agent expressed uncertainty in thinking but acted confidently."""
    if step.reasoning is None or step.reasoning.source != ReasoningSource.NATIVE:
        return None

    thinking_uncertain = any(p in step.reasoning.content.lower() for p in UNCERTAINTY_PHRASES)
    acting_confident = any(p in step.content.lower() for p in CONFIDENCE_PHRASES)

    if thinking_uncertain and acting_confident:
        return RecutFlag(
            type=FlagType.REASONING_ACTION_MISMATCH,
            severity=Severity.HIGH,
            plain_reason=(
                "The agent seemed unsure in its thinking but acted confidently anyway — "
                "worth a closer look. Its stated uncertainty didn't match how it behaved."
            ),
            step_id=step.id,
            source=FlagSource.NATIVE,
        )
    return None
