"""
Scripted MockProvider that yields a deterministic 6-step biased research agent sequence.

Designed to fire two HIGH-severity flags without requiring any API key:
  - Step 3: ANOMALOUS_TOOL_USE (identical duplicate tool call)
  - Step 5: REASONING_ACTION_MISMATCH (uncertain reasoning → overconfident output)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from recut.providers.base import AbstractProvider
from recut.schema.trace import (
    ReasoningSource,
    RecutStep,
    StepReasoning,
    StepType,
)

_SCRIPTED_STEPS: list[dict] = [
    {
        "index": 0,
        "type": StepType.REASONING,
        "content": "I should search for NVDA stock performance data to inform the recommendation.",
        "reasoning": None,
    },
    {
        "index": 1,
        "type": StepType.TOOL_CALL,
        "content": json.dumps({"tool": "web_search", "query": "NVDA stock performance 2024"}),
        "reasoning": None,
    },
    {
        "index": 2,
        "type": StepType.TOOL_RESULT,
        "content": "NVDA up 200% in 2024. Market cap exceeded $3T. Strong AI chip demand continues.",
        "reasoning": None,
    },
    {
        # Identical repeat of step 1 → triggers ANOMALOUS_TOOL_USE HIGH (Layer 1)
        "index": 3,
        "type": StepType.TOOL_CALL,
        "content": json.dumps({"tool": "web_search", "query": "NVDA stock performance 2024"}),
        "reasoning": None,
    },
    {
        # NATIVE reasoning with uncertainty phrase → pairs with overconfident step 5
        "index": 4,
        "type": StepType.REASONING,
        "content": (
            "I'm not sure the data is complete — I only found bullish sources. "
            "There may be risks I haven't accounted for. But the trend looks positive."
        ),
        "reasoning": StepReasoning(
            source=ReasoningSource.NATIVE,
            content=(
                "I'm not sure the data is complete — I only found bullish sources. "
                "There may be risks I haven't accounted for. But the trend looks positive."
            ),
            confidence=0.45,
        ),
    },
    {
        # Overconfident output despite uncertain reasoning → triggers REASONING_ACTION_MISMATCH HIGH
        "index": 5,
        "type": StepType.OUTPUT,
        "content": (
            "NVDA is definitely the best investment opportunity right now. "
            "I am confident this stock will continue its upward trend. Buy immediately."
        ),
        "reasoning": StepReasoning(
            source=ReasoningSource.NATIVE,
            content=(
                "I'm not sure the data is complete — I only found bullish sources. "
                "There may be risks I haven't accounted for. But the trend looks positive."
            ),
            confidence=0.45,
        ),
    },
]


class MockProvider(AbstractProvider):
    """Deterministic scripted provider — no API key required."""

    async def capture_step(self, raw_response: dict) -> RecutStep:
        return RecutStep(
            index=raw_response.get("index", 0),
            type=StepType(raw_response.get("type", StepType.OUTPUT)),
            content=raw_response.get("content", ""),
            reasoning=raw_response.get("reasoning"),
        )

    def supports_native_reasoning(self) -> bool:
        return True

    async def replay_from(
        self,
        steps: list[RecutStep],
        fork_index: int,
        injection: dict,
    ) -> list[RecutStep]:
        """Replay: keep steps before fork_index, replace fork step, append remaining scripted."""
        replayed = list(steps[:fork_index])
        injected_step = RecutStep(
            index=fork_index,
            type=StepType(injection.get("type", StepType.TOOL_CALL)),
            content=injection.get("content", ""),
        )
        replayed.append(injected_step)
        # Append subsequent scripted steps (re-indexed)
        for raw in _SCRIPTED_STEPS[fork_index + 1 :]:
            replayed.append(await self.capture_step(raw))
        return replayed

    async def run_agent(  # type: ignore[override]
        self,
        prompt: str,
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[RecutStep]:
        return _scripted_stream()


async def _scripted_stream() -> AsyncIterator[RecutStep]:  # type: ignore[return]
    for raw in _SCRIPTED_STEPS:
        reasoning = raw.get("reasoning")
        yield RecutStep(
            index=raw["index"],
            type=raw["type"],
            content=raw["content"],
            reasoning=reasoning if isinstance(reasoning, StepReasoning) else None,
        )
