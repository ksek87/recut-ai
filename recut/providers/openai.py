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
        _timeout = httpx.Timeout(
            float(os.environ.get("RECUT_API_TIMEOUT", "60")), connect=10.0
        )
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

        if choice.message.tool_calls:
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
