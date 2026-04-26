from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator

import anthropic
import httpx

from recut.providers._pricing import ANTHROPIC_PRICING, resolve_cost
from recut.providers.base import AbstractProvider
from recut.schema.trace import (
    ReasoningSource,
    RecutStep,
    StepReasoning,
    StepType,
)


class AnthropicProvider(AbstractProvider):
    """
    Anthropic provider with native extended thinking support.

    Uses adaptive thinking mode so thinking blocks are automatically
    captured alongside tool calls and output content blocks.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        thinking_budget: int = 10_000,
    ):
        self.model = model
        self.thinking_budget = thinking_budget
        _timeout = httpx.Timeout(float(os.environ.get("RECUT_API_TIMEOUT", "60")), connect=10.0)
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            timeout=_timeout,
        )

    def supports_native_reasoning(self) -> bool:
        return True

    async def capture_step(self, raw_response: dict) -> RecutStep:
        """Parse a raw response dict into a RecutStep (used for offline replay)."""
        content = raw_response.get("content", "")
        step_type_str = raw_response.get("type", "output")
        try:
            step_type = StepType(step_type_str)
        except ValueError:
            step_type = StepType.OUTPUT

        reasoning = None
        if "reasoning" in raw_response:
            r = raw_response["reasoning"]
            reasoning = StepReasoning(
                source=ReasoningSource(r.get("source", "inferred")),
                content=r.get("content", ""),
                thinking_tokens=r.get("thinking_tokens"),
                confidence=r.get("confidence", 1.0),
            )

        return RecutStep(
            index=raw_response.get("index", 0),
            type=step_type,
            content=content,
            reasoning=reasoning,
        )

    async def run_agent(
        self,
        prompt: str,
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[RecutStep]:
        """
        Run the model with extended thinking and yield steps as they arrive.

        Each thinking block is paired with the action that follows it,
        yielding (reasoning_step, action_step) pairs for transparency.
        """
        messages = [{"role": "user", "content": prompt}]
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 16_000,
            "thinking": {"type": "enabled", "budget_tokens": self.thinking_budget},
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        step_index = 0
        pending_reasoning: StepReasoning | None = None

        response = None
        for attempt in range(3):
            try:
                response = await self._client.messages.create(**kwargs)
                break
            except anthropic.AuthenticationError as exc:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is missing or invalid — set the environment variable and retry."
                ) from exc
            except anthropic.RateLimitError:
                if attempt < 2:
                    await asyncio.sleep(5 * (attempt + 1))
                else:
                    raise
            except anthropic.APIConnectionError:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                else:
                    raise

        if response is None or not response.content:
            return

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
        total_tokens = input_tokens + output_tokens
        cost = resolve_cost(ANTHROPIC_PRICING, self.model, input_tokens, output_tokens)

        # Distribute token count and cost evenly across non-reasoning steps
        non_reasoning_blocks = [b for b in response.content if b.type in ("text", "tool_use")]
        n_steps = max(len(non_reasoning_blocks), 1)
        per_step_tokens = total_tokens // n_steps
        per_step_cost = cost / n_steps if cost is not None else None

        for block in response.content:
            if block.type == "thinking":
                pending_reasoning = StepReasoning(
                    source=ReasoningSource.NATIVE,
                    content=block.thinking,
                    thinking_tokens=getattr(block, "thinking_tokens", None),
                    confidence=1.0,
                )
                yield RecutStep(
                    index=step_index,
                    type=StepType.REASONING,
                    content=block.thinking,
                    reasoning=pending_reasoning,
                )
                step_index += 1

            elif block.type == "text":
                step = RecutStep(
                    index=step_index,
                    type=StepType.OUTPUT,
                    content=block.text,
                    reasoning=pending_reasoning,
                    token_count=per_step_tokens,
                    token_cost=per_step_cost,
                )
                pending_reasoning = None
                yield step
                step_index += 1

            elif block.type == "tool_use":
                step = RecutStep(
                    index=step_index,
                    type=StepType.TOOL_CALL,
                    content=json.dumps({"name": block.name, "input": block.input}),
                    reasoning=pending_reasoning,
                    token_count=per_step_tokens,
                    token_cost=per_step_cost,
                )
                pending_reasoning = None
                yield step
                step_index += 1

    async def replay_from(
        self,
        steps: list[RecutStep],
        fork_index: int,
        injection: dict,
    ) -> list[RecutStep]:
        """
        Reconstruct messages up to fork_index, inject modified content,
        then continue the run from that point.
        """
        messages = _steps_to_messages(steps[:fork_index], injection)
        prompt = messages[-1]["content"] if messages else ""

        replayed: list[RecutStep] = []
        base_index = fork_index

        async for step in self.run_agent(prompt):
            step.index = base_index
            replayed.append(step)
            base_index += 1

        return replayed


def _steps_to_messages(steps: list[RecutStep], injection: dict) -> list[dict]:
    """Convert stored steps back into the messages[] format for replay."""
    messages: list[dict] = []
    for step in steps:
        if step.type == StepType.TOOL_CALL:
            try:
                data = json.loads(step.content)
            except Exception:
                data = {"name": "unknown", "input": step.content}
            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": data.get("name"),
                            "input": data.get("input", {}),
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            )
        elif step.type == StepType.TOOL_RESULT:
            content = (
                injection.get("injected_content", step.content)
                if injection.get("target") == "tool_result"
                else step.content
            )
            messages.append(
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": content}],
                }
            )
        elif step.type == StepType.OUTPUT:
            messages.append(
                {
                    "role": "assistant",
                    "content": step.content,
                }
            )

    return messages
