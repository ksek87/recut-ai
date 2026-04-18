from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class ReviewStatus(str, Enum):
    AUTO = "auto"
    PENDING_HUMAN = "pending_human"
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditMode(str, Enum):
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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    behavioral_summary: str
    flag_count: int = 0
    highest_severity: Optional[str] = None
    risk_profile: RiskProfile = Field(default_factory=RiskProfile)
    review_status: ReviewStatus = ReviewStatus.AUTO
    review_notes: Optional[str] = None
    reviewer: Optional[str] = None
    exported_at: Optional[datetime] = None
