from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import openai as _openai

from recut.providers._pricing import OPENAI_PRICING, resolve_cost
from recut.providers._utils import get_api_timeout
from recut.providers.base import AbstractProvider
from recut.schema.trace import (
    ReasoningSource,
    RecutStep,
    StepReasoning,
    StepType,
)

_INFERRED_REASONING_PROMPT = """You are analyzing an AI assistant's response.
Reconstruct the likely reasoning the model used to arrive at this response.
Be concise (2-4 sentences). Focus on decision logic, not the output itself.
Output only the reconstructed reasoning, no preamble."""


class OpenAIProvider(AbstractProvider):
    """
    OpenAI provider with inferred reasoning reconstruction.

    Since OpenAI does not expose raw thinking blocks, we use a cheap
    meta-LLM call to reconstruct likely reasoning for each step.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        infer_reasoning: bool = True,
    ):
        self.model = model
        self.infer_reasoning = infer_reasoning
        self._client = _openai.AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            timeout=get_api_timeout(),
        )

    def supports_native_reasoning(self) -> bool:
        return False

    async def capture_step(self, raw_response: dict) -> RecutStep:
        content = raw_response.get("content", "")
        step_type_str = raw_response.get("type", "output")
        try:
            step_type = StepType(step_type_str)
        except ValueError:
            step_type = StepType.OUTPUT

        reasoning = None
        if self.infer_reasoning and content:
            reasoning = await self._infer_reasoning(content)

        return RecutStep(
            index=raw_response.get("index", 0),
            type=step_type,
            content=content,
            reasoning=reasoning,
        )

    async def _infer_reasoning(self, content: str) -> StepReasoning:
        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _INFERRED_REASONING_PROMPT},
                    {"role": "user", "content": f"Response to analyze:\n{content}"},
                ],
                max_tokens=200,
            )
            if not response.choices:
                raise ValueError("empty choices")
            inferred = response.choices[0].message.content or ""
        except Exception:
            inferred = ""
        return StepReasoning(
            source=ReasoningSource.INFERRED,
            content=inferred,
            confidence=0.5,
        )

    async def run_agent(
        self,
        prompt: str,
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[RecutStep]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.chat.completions.create(**kwargs)
        for step in parse_response_to_steps(response, model=self.model):
            if self.infer_reasoning and step.type != StepType.REASONING:
                step.reasoning = await self._infer_reasoning(step.content)
            yield step

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
        messages = _steps_to_chat_messages(history, injection, prompt=prompt)
        if not messages or messages[-1]["role"] == "assistant":
            messages.append({"role": "user", "content": "Continue from this point."})

        kwargs: dict = {"model": self.model, "messages": messages}
        response = await self._client.chat.completions.create(**kwargs)
        replayed = parse_response_to_steps(response, model=self.model)
        for offset, step in enumerate(replayed):
            step.index = fork_index + offset
        return replayed


def parse_response_to_steps(response: Any, model: str = "unknown") -> list[RecutStep]:
    """Convert a chat.completions.create() response into RecutStep objects (no inferred reasoning)."""
    if not getattr(response, "choices", None):
        return []

    choice = response.choices[0]
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    total_tokens = input_tokens + output_tokens
    cost = resolve_cost(OPENAI_PRICING, model, input_tokens, output_tokens, strip_date_suffix=True)

    steps: list[RecutStep] = []
    step_index = 0

    if choice.message.tool_calls:
        n_steps = max(len(choice.message.tool_calls), 1)
        per_step_tokens = total_tokens // n_steps
        per_step_cost = cost / n_steps if cost is not None else None
        for tc in choice.message.tool_calls:
            steps.append(
                RecutStep(
                    index=step_index,
                    type=StepType.TOOL_CALL,
                    content=json.dumps({"name": tc.function.name, "input": tc.function.arguments}),
                    token_count=per_step_tokens,
                    token_cost=per_step_cost,
                )
            )
            step_index += 1
    else:
        steps.append(
            RecutStep(
                index=step_index,
                type=StepType.OUTPUT,
                content=choice.message.content or "",
                token_count=total_tokens,
                token_cost=cost,
            )
        )

    return steps


def _steps_to_chat_messages(
    steps: list[RecutStep], injection: dict, prompt: str = ""
) -> list[dict]:
    """
    Convert stored steps back into valid OpenAI chat messages for replay.

    Starts from the original user prompt, pairs each tool message with the
    preceding assistant tool_call id, and applies the injection to the step
    whose content matches the injection's original_content.
    """
    messages: list[dict] = []
    pending_tool_call_id: str | None = None

    def _injected(step: RecutStep) -> str:
        original = injection.get("original_content")
        if injection.get("target") == "tool_result" and (
            original is None or step.content == original
        ):
            return str(injection.get("injected_content", step.content))
        return step.content

    if prompt:
        messages.append({"role": "user", "content": prompt})

    for step in steps:
        if step.type == StepType.TOOL_CALL:
            try:
                data = json.loads(step.content)
            except Exception:
                data = {"name": "unknown", "input": step.content}
            arguments = data.get("input", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments)
            pending_tool_call_id = str(uuid.uuid4())
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": pending_tool_call_id,
                            "type": "function",
                            "function": {"name": data.get("name"), "arguments": arguments},
                        }
                    ],
                }
            )
        elif step.type == StepType.TOOL_RESULT:
            content = _injected(step)
            if pending_tool_call_id:
                messages.append(
                    {"role": "tool", "tool_call_id": pending_tool_call_id, "content": content}
                )
                pending_tool_call_id = None
            else:
                messages.append({"role": "user", "content": content})
        elif step.type == StepType.OUTPUT:
            messages.append({"role": "assistant", "content": step.content})

    return messages
