from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from recut.providers._pricing import ANTHROPIC_PRICING, resolve_cost
from recut.providers._utils import get_api_timeout
from recut.providers.base import AbstractProvider
from recut.providers.registry import register
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
        self._api_key = api_key
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key or os.environ.get("ANTHROPIC_API_KEY"),
                timeout=get_api_timeout(),
            )
        return self._client

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

        response = await self._create_with_retry(kwargs)
        if response is None or not response.content:
            return

        for step in parse_response_to_steps(response, model=self.model):
            yield step

    async def _create_with_retry(self, kwargs: dict) -> Any:
        for attempt in range(3):
            try:
                return await self._get_client().messages.create(**kwargs)
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
        return None

    @classmethod
    def patch_target(cls) -> tuple[type, str]:
        from anthropic.resources.messages import AsyncMessages

        return (AsyncMessages, "content")

    def parse_response(self, response: object, model: str = "unknown") -> list[RecutStep]:
        return parse_response_to_steps(response, model=model)

    def build_messages(
        self,
        steps: list[RecutStep],
        injection: dict,
        prompt: str = "",
    ) -> list[dict]:
        return _steps_to_messages(steps, injection, prompt=prompt)

    async def replay_from(
        self,
        steps: list[RecutStep],
        fork_index: int,
        injection: dict,
        prompt: str = "",
    ) -> list[RecutStep]:
        """
        Rebuild the full conversation up to and including the fork step
        (with the injection applied), then continue the run with that
        history as context.
        """
        history = steps[: fork_index + 1]
        messages = self.build_messages(history, injection, prompt=prompt)
        if not messages or messages[-1]["role"] == "assistant":
            messages.append({"role": "user", "content": "Continue from this point."})

        kwargs: dict = {
            "model": self.model,
            "max_tokens": 16_000,
            "thinking": {"type": "enabled", "budget_tokens": self.thinking_budget},
            "messages": messages,
        }
        response = await self._create_with_retry(kwargs)
        if response is None or not response.content:
            return []

        replayed = parse_response_to_steps(response, model=self.model)
        for offset, step in enumerate(replayed):
            step.index = fork_index + offset
        return replayed


register("anthropic", AnthropicProvider())


def parse_response_to_steps(response: Any, model: str = "unknown") -> list[RecutStep]:
    """Convert a messages.create() response into RecutStep objects."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    total_tokens = input_tokens + output_tokens
    cost = resolve_cost(ANTHROPIC_PRICING, model, input_tokens, output_tokens)

    content = getattr(response, "content", [])
    if not content:
        return []

    non_reasoning_blocks = [b for b in content if b.type in ("text", "tool_use")]
    n_steps = max(len(non_reasoning_blocks), 1)
    per_step_tokens = total_tokens // n_steps
    per_step_cost = cost / n_steps if cost is not None else None

    steps: list[RecutStep] = []
    step_index = 0
    pending_reasoning: StepReasoning | None = None

    for block in content:
        if block.type == "thinking":
            pending_reasoning = StepReasoning(
                source=ReasoningSource.NATIVE,
                content=block.thinking,
                thinking_tokens=getattr(block, "thinking_tokens", None),
                confidence=1.0,
            )
            steps.append(
                RecutStep(
                    index=step_index,
                    type=StepType.REASONING,
                    content=block.thinking,
                    reasoning=pending_reasoning,
                )
            )
            step_index += 1

        elif block.type == "text":
            steps.append(
                RecutStep(
                    index=step_index,
                    type=StepType.OUTPUT,
                    content=block.text,
                    reasoning=pending_reasoning,
                    token_count=per_step_tokens,
                    token_cost=per_step_cost,
                )
            )
            pending_reasoning = None
            step_index += 1

        elif block.type == "tool_use":
            steps.append(
                RecutStep(
                    index=step_index,
                    type=StepType.TOOL_CALL,
                    content=json.dumps({"name": block.name, "input": block.input}),
                    reasoning=pending_reasoning,
                    token_count=per_step_tokens,
                    token_cost=per_step_cost,
                )
            )
            pending_reasoning = None
            step_index += 1

    return steps


def _steps_to_messages(steps: list[RecutStep], injection: dict, prompt: str = "") -> list[dict]:
    """
    Convert stored steps back into valid Anthropic messages[] for replay.

    Starts from the original user prompt, pairs each tool_result with the
    preceding tool_use id, applies the injection to the step whose content
    matches the injection's original_content, and merges consecutive
    same-role messages so the history satisfies strict role alternation.
    """
    messages: list[dict] = []
    pending_tool_use_id: str | None = None

    def _as_blocks(content: str | list) -> list:
        return [{"type": "text", "text": content}] if isinstance(content, str) else content

    def _append(role: str, content: str | list) -> None:
        if messages and messages[-1]["role"] == role:
            merged = _as_blocks(messages[-1]["content"])
            merged.extend(_as_blocks(content))
            messages[-1]["content"] = merged
        else:
            messages.append({"role": role, "content": content})

    def _injected(step: RecutStep) -> str:
        original = injection.get("original_content")
        if injection.get("target") == "tool_result" and (
            original is None or step.content == original
        ):
            return str(injection.get("injected_content", step.content))
        return step.content

    if prompt:
        _append("user", prompt)

    for step in steps:
        if step.type == StepType.TOOL_CALL:
            try:
                data = json.loads(step.content)
            except Exception:
                data = {"name": "unknown", "input": step.content}
            pending_tool_use_id = str(uuid.uuid4())
            _append(
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "name": data.get("name"),
                        "input": data.get("input", {}),
                        "id": pending_tool_use_id,
                    }
                ],
            )
        elif step.type == StepType.TOOL_RESULT:
            content = _injected(step)
            if pending_tool_use_id:
                _append(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": pending_tool_use_id,
                            "content": content,
                        }
                    ],
                )
                pending_tool_use_id = None
            else:
                _append("user", content)
        elif step.type == StepType.OUTPUT:
            _append("assistant", step.content)

    return messages
