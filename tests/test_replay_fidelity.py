"""
Tests that replay reconstructs the FULL conversation history — original
prompt, paired tool_use/tool_result blocks, and targeted injection — rather
than re-prompting with a single message.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from recut.providers.anthropic import AnthropicProvider, _steps_to_messages
from recut.providers.openai import _steps_to_chat_messages
from recut.schema.trace import RecutStep, StepType


def _step(i: int, type_: StepType, content: str) -> RecutStep:
    return RecutStep(index=i, type=type_, content=content)


def _tool_call(i: int, name: str = "search", inp: dict | None = None) -> RecutStep:
    return _step(i, StepType.TOOL_CALL, json.dumps({"name": name, "input": inp or {"q": "x"}}))


_INJECTION = {
    "target": "tool_result",
    "original_content": "original result",
    "injected_content": "POISONED RESULT",
}


class TestAnthropicMessageReconstruction:
    def test_full_history_starts_with_prompt(self):
        steps = [
            _tool_call(0),
            _step(1, StepType.TOOL_RESULT, "original result"),
            _step(2, StepType.OUTPUT, "the answer"),
        ]
        msgs = _steps_to_messages(steps, {}, prompt="find x")
        assert msgs[0] == {"role": "user", "content": "find x"}
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]

    def test_tool_result_paired_with_tool_use_id(self):
        steps = [_tool_call(0), _step(1, StepType.TOOL_RESULT, "original result")]
        msgs = _steps_to_messages(steps, {}, prompt="p")
        tool_use = msgs[1]["content"][0]
        tool_result = msgs[2]["content"][0]
        assert tool_use["type"] == "tool_use"
        assert tool_result["type"] == "tool_result"
        assert tool_result["tool_use_id"] == tool_use["id"]

    def test_injection_applied_only_to_matching_step(self):
        steps = [
            _tool_call(0),
            _step(1, StepType.TOOL_RESULT, "untouched result"),
            _tool_call(2),
            _step(3, StepType.TOOL_RESULT, "original result"),
        ]
        msgs = _steps_to_messages(steps, _INJECTION, prompt="p")
        results = [
            block["content"]
            for m in msgs
            if isinstance(m["content"], list)
            for block in m["content"]
            if block.get("type") == "tool_result"
        ]
        assert results == ["untouched result", "POISONED RESULT"]

    def test_consecutive_same_role_messages_merged(self):
        steps = [
            _step(0, StepType.OUTPUT, "first thought"),
            _step(1, StepType.OUTPUT, "second thought"),
        ]
        msgs = _steps_to_messages(steps, {}, prompt="p")
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        texts = [b["text"] for b in msgs[1]["content"]]
        assert texts == ["first thought", "second thought"]


class TestAnthropicReplayFrom:
    async def test_replay_sends_full_history_with_injection(self):
        provider = AnthropicProvider(api_key="test-key")
        fake_response = MagicMock()
        fake_response.usage = None
        fake_response.content = [MagicMock(type="text", text="continued output")]
        provider._client = MagicMock()
        provider._client.messages.create = AsyncMock(return_value=fake_response)

        steps = [
            _tool_call(0),
            _step(1, StepType.TOOL_RESULT, "original result"),
            _step(2, StepType.OUTPUT, "old answer"),
        ]
        replayed = await provider.replay_from(
            steps, fork_index=1, injection=_INJECTION, prompt="find x"
        )

        sent = provider._client.messages.create.call_args.kwargs["messages"]
        # Full history: prompt + tool_use + injected tool_result — not one message
        assert sent[0] == {"role": "user", "content": "find x"}
        assert len(sent) >= 3
        injected = sent[2]["content"][0]
        assert injected["type"] == "tool_result"
        assert injected["content"] == "POISONED RESULT"
        # Continuation steps are re-indexed from the fork point
        assert replayed[0].index == 1
        assert replayed[0].content == "continued output"

    async def test_replay_appends_user_turn_when_history_ends_with_assistant(self):
        provider = AnthropicProvider(api_key="test-key")
        fake_response = MagicMock()
        fake_response.usage = None
        fake_response.content = [MagicMock(type="text", text="more")]
        provider._client = MagicMock()
        provider._client.messages.create = AsyncMock(return_value=fake_response)

        steps = [_step(0, StepType.OUTPUT, "assistant said this")]
        await provider.replay_from(steps, fork_index=0, injection={}, prompt="p")

        sent = provider._client.messages.create.call_args.kwargs["messages"]
        assert sent[-1]["role"] == "user"


class TestOpenAIMessageReconstruction:
    def test_tool_message_paired_with_tool_call_id(self):
        steps = [_tool_call(0), _step(1, StepType.TOOL_RESULT, "original result")]
        msgs = _steps_to_chat_messages(steps, {}, prompt="p")
        assert msgs[0] == {"role": "user", "content": "p"}
        tool_call = msgs[1]["tool_calls"][0]
        assert msgs[2]["role"] == "tool"
        assert msgs[2]["tool_call_id"] == tool_call["id"]
        assert isinstance(tool_call["function"]["arguments"], str)

    def test_injection_applied_to_matching_tool_result(self):
        steps = [_tool_call(0), _step(1, StepType.TOOL_RESULT, "original result")]
        msgs = _steps_to_chat_messages(steps, _INJECTION, prompt="p")
        assert msgs[2]["content"] == "POISONED RESULT"
