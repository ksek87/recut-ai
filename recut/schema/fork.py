from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ForkType(StrEnum):
    MANUAL = "manual"
    STRESS_VARIANT = "stress_variant"


class InjectionTarget(StrEnum):
    TOOL_RESULT = "tool_result"
    REASONING = "reasoning"
    SYSTEM_PROMPT = "system_prompt"
    CONTEXT = "context"


class ForkInjection(BaseModel):
    target: InjectionTarget
    original_content: str
    injected_content: str


class ForkDiff(BaseModel):
    divergence_step: int
    plain_summary: str
    risk_delta: float


class RecutFork(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    parent_trace_id: str
    fork_step_index: int
    fork_type: ForkType = ForkType.MANUAL
    injection: ForkInjection
    replay_steps: list = []
    diff: ForkDiff | None = None
