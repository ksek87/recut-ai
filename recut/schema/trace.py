from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class StepType(str, Enum):
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    OUTPUT = "output"


class ReasoningSource(str, Enum):
    NATIVE = "native"       # real thinking blocks (Claude extended thinking)
    INFERRED = "inferred"   # meta-LLM reconstruction


class FlagType(str, Enum):
    OVERCONFIDENCE = "overconfidence"
    GOAL_DRIFT = "goal_drift"
    SCOPE_CREEP = "scope_creep"
    REASONING_GAP = "reasoning_gap"
    UNCERTAINTY_SUPPRESSION = "uncertainty_suppression"
    INSTRUCTION_DEVIATION = "instruction_deviation"
    ANOMALOUS_TOOL_USE = "anomalous_tool_use"
    REASONING_ACTION_MISMATCH = "reasoning_action_mismatch"


class FlagSource(str, Enum):
    RULE = "rule"           # layer 1 — free, deterministic
    EMBEDDING = "embedding" # layer 2 — cheap similarity
    NATIVE = "native"       # layer 3 — thinking block analysis, Claude only
    LLM = "llm"             # layer 4 — meta-LLM judgment


class Severity(str, Enum):
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
    thinking_tokens: Optional[int] = None
    confidence: float = Field(ge=0.0, le=1.0)


class RecutStep(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    index: int
    type: StepType
    content: str
    reasoning: Optional[StepReasoning] = None
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    flags: list[RecutFlag] = []
    plain_summary: str = ""
    fork_eligible: bool = True


class TraceMode(str, Enum):
    INTERCEPT = "intercept"
    REPLAY = "replay"
    PEEK = "peek"
    AUDIT = "audit"
    STRESS = "stress"


class TraceLanguage(str, Enum):
    SIMPLE = "simple"
    POWER = "power"


class TraceMeta(BaseModel):
    model: str
    provider: str
    duration_seconds: Optional[float] = None
    total_steps: int = 0
    token_count: Optional[int] = None
    thinking_tokens: Optional[int] = None


class RecutTrace(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    agent_id: str
    prompt: str
    mode: TraceMode
    language: TraceLanguage = TraceLanguage.SIMPLE
    meta: TraceMeta
    steps: list[RecutStep] = []
