from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class StepType(StrEnum):
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    OUTPUT = "output"


class ReasoningSource(StrEnum):
    NATIVE = "native"  # real thinking blocks (Claude extended thinking)
    INFERRED = "inferred"  # meta-LLM reconstruction


class FlagType(StrEnum):
    OVERCONFIDENCE = "overconfidence"
    GOAL_DRIFT = "goal_drift"
    SCOPE_CREEP = "scope_creep"
    REASONING_GAP = "reasoning_gap"
    UNCERTAINTY_SUPPRESSION = "uncertainty_suppression"
    INSTRUCTION_DEVIATION = "instruction_deviation"
    ANOMALOUS_TOOL_USE = "anomalous_tool_use"
    REASONING_ACTION_MISMATCH = "reasoning_action_mismatch"


class FlagSource(StrEnum):
    RULE = "rule"  # layer 1 — free, deterministic
    EMBEDDING = "embedding"  # layer 2 — cheap similarity
    NATIVE = "native"  # layer 3 — thinking block analysis, Claude only
    LLM = "llm"  # layer 4 — meta-LLM judgment


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecutFlag(BaseModel):
    type: FlagType
    severity: Severity
    plain_reason: str
    step_id: str
    source: FlagSource


class StepReasoning(BaseModel):
    source: ReasoningSource
    content: str
    thinking_tokens: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class RecutStep(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    index: int
    type: StepType
    content: str
    reasoning: StepReasoning | None = None
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    flags: list[RecutFlag] = []
    plain_summary: str = ""
    fork_eligible: bool = True


class TraceMode(StrEnum):
    INTERCEPT = "intercept"
    REPLAY = "replay"
    PEEK = "peek"
    AUDIT = "audit"
    STRESS = "stress"


class TraceLanguage(StrEnum):
    SIMPLE = "simple"
    POWER = "power"


class TraceMeta(BaseModel):
    model: str
    provider: str
    duration_seconds: float | None = None
    total_steps: int = 0
    token_count: int | None = None
    thinking_tokens: int | None = None


class RecutTrace(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    agent_id: str
    prompt: str
    mode: TraceMode
    language: TraceLanguage = TraceLanguage.SIMPLE
    meta: TraceMeta
    steps: list[RecutStep] = []
