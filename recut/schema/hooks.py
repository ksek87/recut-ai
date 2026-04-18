from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from recut.schema.trace import RecutFlag, RecutStep


class SuggestedAction(str):
    PEEK = "peek"
    AUDIT = "audit"
    REPLAY = "replay"
    ESCALATE = "escalate"


class RecutFlagEvent(BaseModel):
    trace_id: str
    step_id: str
    flag: RecutFlag
    suggested_action: str
    preceding_steps: list[RecutStep] = []
    agent_id: str


FlagHandler = Callable[[RecutFlagEvent], None]
