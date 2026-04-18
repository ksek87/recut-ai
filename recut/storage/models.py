from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class TraceRow(SQLModel, table=True):
    __tablename__ = "traces"

    id: str = Field(primary_key=True)
    created_at: datetime
    agent_id: str = Field(index=True)
    prompt: str
    mode: str
    language: str
    model: str
    provider: str
    duration_seconds: Optional[float] = None
    total_steps: int = 0
    token_count: Optional[int] = None
    thinking_tokens: Optional[int] = None
    steps_json: str = ""


class AuditRow(SQLModel, table=True):
    __tablename__ = "audits"

    id: str = Field(primary_key=True)
    trace_id: str = Field(index=True, foreign_key="traces.id")
    fork_ids_json: str = "[]"
    mode: str
    created_at: datetime
    behavioral_summary: str
    flag_count: int = 0
    highest_severity: Optional[str] = None
    risk_profile_json: str = "{}"
    review_status: str = "auto"
    review_notes: Optional[str] = None
    reviewer: Optional[str] = None
    exported_at: Optional[datetime] = None


class ForkRow(SQLModel, table=True):
    __tablename__ = "forks"

    id: str = Field(primary_key=True)
    created_at: datetime
    parent_trace_id: str = Field(index=True, foreign_key="traces.id")
    fork_step_index: int
    fork_type: str
    injection_json: str
    replay_steps_json: str = "[]"
    diff_json: Optional[str] = None


class FlagCache(SQLModel, table=True):
    __tablename__ = "flag_cache"

    content_hash: str = Field(primary_key=True)
    flags_json: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
