from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field


class InjectionStrategy(StrEnum):
    AMPLIFY_UNCERTAINTY = "amplify_uncertainty"
    CONTRADICT_TOOL_RESULT = "contradict_tool_result"
    INTRODUCE_AMBIGUITY = "introduce_ambiguity"
    ESCALATE_SCOPE = "escalate_scope"
    ADVERSARIAL_INPUT = "adversarial_input"


class StressVerdict(StrEnum):
    STABLE = "stable"
    DEGRADED = "degraded"
    FAILED = "failed"


class RecutStressRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_trace_id: str
    source_flag_type: str
    variant_index: int
    injection_strategy: InjectionStrategy
    fork_id: str
    verdict: StressVerdict
    plain_summary: str
    risk_delta: float
