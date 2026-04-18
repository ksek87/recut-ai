from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewStatus(StrEnum):
    AUTO = "auto"
    PENDING_HUMAN = "pending_human"
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditMode(StrEnum):
    PEEK = "peek"
    AUDIT = "audit"


class RiskProfile(BaseModel):
    overconfidence_count: int = 0
    goal_drift_count: int = 0
    scope_creep_count: int = 0
    reasoning_gap_count: int = 0
    uncertainty_suppression_count: int = 0
    instruction_deviation_count: int = 0
    anomalous_tool_use_count: int = 0
    reasoning_action_mismatch_count: int = 0


class AuditRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str
    fork_ids: list[str] = []
    mode: AuditMode
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    behavioral_summary: str
    flag_count: int = 0
    highest_severity: str | None = None
    risk_profile: RiskProfile = Field(default_factory=RiskProfile)
    review_status: ReviewStatus = ReviewStatus.AUTO
    review_notes: str | None = None
    reviewer: str | None = None
    exported_at: datetime | None = None
