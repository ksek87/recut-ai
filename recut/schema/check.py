from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CheckOutcome(BaseModel):
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str


class CheckReport(BaseModel):
    agent_id: str
    trace_id: str
    baseline_trace_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    passed: bool
    first_run: bool = False
    checks: list[CheckOutcome] = []
