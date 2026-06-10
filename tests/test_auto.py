"""Tests for recut.init() — zero-change SDK instrumentation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import recut
from recut.auto import _extract_prompt, init, uninstall


class TestExtractPrompt:
    def test_plain_string(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert _extract_prompt(msgs) == "hello"

    def test_multipart_content(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": "world"},
                ],
            }
        ]
        assert _extract_prompt(msgs) == "hello world"

    def test_last_user_message(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        assert _extract_prompt(msgs) == "second"

    def test_empty_messages(self):
        assert _extract_prompt([]) == ""

    def test_no_user_message(self):
        msgs = [{"role": "assistant", "content": "hi"}]
        assert _extract_prompt(msgs) == ""

    def test_non_text_parts_skipped(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "url": "http://..."},
                    {"type": "text", "text": "describe this"},
                ],
            }
        ]
        assert _extract_prompt(msgs) == "describe this"


class TestInitPatching:
    def setup_method(self):
        uninstall()

    def teardown_method(self):
        uninstall()

    def test_init_patches_anthropic(self):
        from anthropic.resources.messages import AsyncMessages

        original = AsyncMessages.create
        init(agent_id="test")
        assert AsyncMessages.create is not original

    def test_init_patches_openai(self):
        from openai.resources.chat.completions import AsyncCompletions

        original = AsyncCompletions.create
        init(agent_id="test")
        assert AsyncCompletions.create is not original

    def test_init_idempotent(self):
        from anthropic.resources.messages import AsyncMessages

        init(agent_id="test")
        patched_once = AsyncMessages.create
        init(agent_id="test")  # second call — must not double-wrap
        assert AsyncMessages.create is patched_once

    def test_uninstall_restores_originals(self):
        from anthropic.resources.messages import AsyncMessages
        from openai.resources.chat.completions import AsyncCompletions

        anthropic_original = AsyncMessages.create
        openai_original = AsyncCompletions.create
        init(agent_id="test")
        uninstall()
        assert AsyncMessages.create is anthropic_original
        assert AsyncCompletions.create is openai_original

    async def test_auto_capture_creates_trace(self):
        from recut.auto import _capture
        from recut.schema.trace import TraceMode

        fake_response = MagicMock()
        fake_response.usage = None
        fake_response.content = [MagicMock(type="text", text="hello")]

        with (
            patch("recut.auto.write_queue.enqueue", new=AsyncMock()) as mock_enqueue,
            patch(
                "recut.providers.anthropic.parse_response_to_steps",
                return_value=[MagicMock()],
            ),
            patch("recut.auto._persist_trace", new=AsyncMock()),
        ):
            await _capture(
                fake_response,
                {"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}]},
                "my-agent",
                TraceMode.PEEK,
                "anthropic",
            )
        mock_enqueue.assert_called_once()

    async def test_auto_capture_silent_on_error(self):
        from recut.auto import _capture
        from recut.schema.trace import TraceMode

        fake_response = MagicMock(spec=[])  # no .content — triggers error path

        # Must not raise
        await _capture(fake_response, {}, "agent", TraceMode.PEEK, "anthropic")

    def test_init_exposed_on_recut(self):
        assert callable(recut.init)
