from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx
import openai as _openai

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

# Pricing per million tokens (input, output)
_OPENAI_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.0, 60.0),
    "o1-mini": (3.0, 12.0),
    "o3-mini": (1.10, 4.40),
}


def _openai_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    pricing = _OPENAI_PRICING.get(model)
    if pricing is None:
        # Strip date suffix like "-2024-11-20" from "gpt-4o-2024-11-20"
        parts = model.split("-")
        for i, part in enumerate(parts):
            if len(part) == 4 and part.isdigit():
                pricing = _OPENAI_PRICING.get("-".join(parts[:i]))
                break
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


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
        _timeout = httpx.Timeout(float(os.environ.get("RECUT_API_TIMEOUT", "60")), connect=10.0)
        self._client = _openai.AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            timeout=_timeout,
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

        step_index = 0
        response = await self._client.chat.completions.create(**kwargs)
        if not response.choices:
            return
        choice = response.choices[0]

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        total_tokens = input_tokens + output_tokens
        cost = _openai_cost(self.model, input_tokens, output_tokens)

        if choice.message.tool_calls:
            n_steps = max(len(choice.message.tool_calls), 1)
            per_step_tokens = total_tokens // n_steps
            per_step_cost = cost / n_steps if cost is not None else None

            for tc in choice.message.tool_calls:
                reasoning = (
                    await self._infer_reasoning(json.dumps(tc.function.__dict__))
                    if self.infer_reasoning
                    else None
                )
                yield RecutStep(
                    index=step_index,
                    type=StepType.TOOL_CALL,
                    content=json.dumps({"name": tc.function.name, "input": tc.function.arguments}),
                    reasoning=reasoning,
                    token_count=per_step_tokens,
                    token_cost_usd=per_step_cost,
                )
                step_index += 1
        else:
            content = choice.message.content or ""
            reasoning = await self._infer_reasoning(content) if self.infer_reasoning else None
            yield RecutStep(
                index=step_index,
                type=StepType.OUTPUT,
                content=content,
                reasoning=reasoning,
                token_count=total_tokens,
                token_cost_usd=cost,
            )

    async def replay_from(
        self,
        steps: list[RecutStep],
        fork_index: int,
        injection: dict,
    ) -> list[RecutStep]:
        injected_prompt = injection.get("injected_content", "")
        replayed: list[RecutStep] = []
        base_index = fork_index
        async for step in self.run_agent(injected_prompt):
            step.index = base_index
            replayed.append(step)
            base_index += 1
        return replayed
