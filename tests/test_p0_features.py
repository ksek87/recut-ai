"""
Tests for P0 release blocker features:
  Issue #18 — Layer 4 BYOM backend dispatcher (RECUT_L4_BACKEND)
  Issue #19 — @recut.on_flag with severity/flag_type filters, wired to all modes
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import recut
from recut.flagging.engine import _call_l4_api, _get_l4_client, _layer4_llm_judge
from recut.hooks import fire_all, get_all, matches, register
from recut.schema.hooks import RecutFlagEvent
from recut.schema.trace import (
    FlagSource,
    FlagType,
    RecutFlag,
    RecutStep,
    RecutTrace,
    Severity,
    StepType,
    TraceMeta,
    TraceLanguage,
    TraceMode,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_l4_clients():
    """Prevent L4 client singletons from leaking between tests."""
    import recut.flagging.engine as eng

    eng._l4_clients.clear()
    yield
    eng._l4_clients.clear()


@pytest.fixture(autouse=True)
def _clear_hook_registry():
    """Prevent registered handlers from leaking between tests."""
    import recut.hooks as hk

    saved = list(hk._registry)
    hk._registry.clear()
    yield
    hk._registry.clear()
    hk._registry.extend(saved)


def _make_step(
    index: int,
    step_type: StepType = StepType.OUTPUT,
    content: str = "hello",
    *,
    step_id: str | None = None,
) -> RecutStep:
    return RecutStep(
        id=step_id or f"step-{index}",
        index=index,
        type=step_type,
        content=content,
    )


def _make_trace(*steps: RecutStep) -> RecutTrace:
    return RecutTrace(
        agent_id="test-agent",
        prompt="test prompt",
        mode=TraceMode.AUDIT,
        language=TraceLanguage.SIMPLE,
        meta=TraceMeta(model="test-model", provider="test-provider"),
        steps=list(steps),
    )


def _make_flag(
    *,
    flag_type: FlagType = FlagType.OVERCONFIDENCE,
    severity: Severity = Severity.HIGH,
    step_id: str = "step-0",
) -> RecutFlag:
    return RecutFlag(
        type=flag_type,
        severity=severity,
        plain_reason="test flag",
        step_id=step_id,
        source=FlagSource.RULE,
    )


def _make_flag_event(
    *,
    flag_type: FlagType = FlagType.OVERCONFIDENCE,
    severity: Severity = Severity.HIGH,
) -> RecutFlagEvent:
    return RecutFlagEvent(
        trace_id="trace-1",
        step_id="step-0",
        flag=_make_flag(flag_type=flag_type, severity=severity),
        suggested_action="peek",
        agent_id="agent-1",
    )


# ===========================================================================
# Issue #18 — BYOM backend dispatcher
# ===========================================================================


class TestByomClientFactory:
    def test_anthropic_backend_creates_anthropic_client(self):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = _get_l4_client("anthropic")
            mock_cls.assert_called_once()
            assert client is mock_cls.return_value

    def test_local_backend_creates_openai_client_with_base_url(self, monkeypatch):
        monkeypatch.setenv("RECUT_L4_LOCAL_URL", "http://localhost:11434/v1")
        with patch("openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = _get_l4_client("local")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["base_url"] == "http://localhost:11434/v1"
            assert call_kwargs["api_key"] == "local"
            assert client is mock_cls.return_value

    def test_openai_backend_creates_openai_client_without_base_url(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = _get_l4_client("openai")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert "base_url" not in call_kwargs or call_kwargs.get("base_url") is None
            assert call_kwargs["api_key"] == "sk-test"

    def test_client_is_reused_as_singleton(self):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value = MagicMock()
            c1 = _get_l4_client("anthropic")
            c2 = _get_l4_client("anthropic")
            mock_cls.assert_called_once()  # constructed only once
            assert c1 is c2


class TestByomCallDispatch:
    async def test_anthropic_backend_calls_messages_create(self):
        mock_block = MagicMock()
        mock_block.text = '{"flags": []}'
        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("recut.flagging.engine._get_l4_client", return_value=mock_client):
            result = await _call_l4_api("anthropic", "system", "user prompt", "claude-haiku")

        mock_client.messages.create.assert_called_once()
        assert result == '{"flags": []}'

    async def test_openai_backend_calls_chat_completions_create(self):
        mock_choice = MagicMock()
        mock_choice.message.content = '{"flags": []}'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("recut.flagging.engine._get_l4_client", return_value=mock_client):
            result = await _call_l4_api("local", "system", "user prompt", "llama3")

        mock_client.chat.completions.create.assert_called_once()
        assert result == '{"flags": []}'

    async def test_anthropic_empty_content_returns_empty_string(self):
        mock_response = MagicMock()
        mock_response.content = []
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("recut.flagging.engine._get_l4_client", return_value=mock_client):
            result = await _call_l4_api("anthropic", "system", "prompt", "model")

        assert result == ""

    async def test_openai_empty_choices_returns_empty_string(self):
        mock_response = MagicMock()
        mock_response.choices = []
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("recut.flagging.engine._get_l4_client", return_value=mock_client):
            result = await _call_l4_api("local", "system", "prompt", "model")

        assert result == ""


class TestByomLlmJudge:
    async def test_unknown_backend_defaults_to_local(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("RECUT_L4_BACKEND", "bogus-backend")
        step = _make_step(0)

        with patch("recut.flagging.engine._call_l4_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "[]"
            with caplog.at_level(logging.WARNING, logger="recut.flagging.engine"):
                await _layer4_llm_judge([step], "test prompt")

        assert "bogus-backend" in caplog.text
        called_backend = mock_call.call_args.args[0]
        assert called_backend == "local"

    async def test_default_model_per_backend(self, monkeypatch):
        monkeypatch.setenv("RECUT_L4_BACKEND", "anthropic")
        monkeypatch.delenv("RECUT_META_MODEL", raising=False)
        step = _make_step(0)

        with patch("recut.flagging.engine._call_l4_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "[]"
            await _layer4_llm_judge([step], "prompt")

        called_model = mock_call.call_args.args[3]
        assert "haiku" in called_model

    async def test_meta_model_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("RECUT_L4_BACKEND", "openai")
        monkeypatch.setenv("RECUT_META_MODEL", "gpt-4o")
        step = _make_step(0)

        with patch("recut.flagging.engine._call_l4_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "[]"
            await _layer4_llm_judge([step], "prompt")

        called_model = mock_call.call_args.args[3]
        assert called_model == "gpt-4o"

    async def test_local_connection_error_returns_empty_silently(self, monkeypatch, caplog):
        import logging

        import openai

        monkeypatch.setenv("RECUT_L4_BACKEND", "local")
        step = _make_step(0)

        with patch(
            "recut.flagging.engine._call_l4_api",
            side_effect=openai.APIConnectionError(request=MagicMock()),
        ):
            with caplog.at_level(logging.DEBUG, logger="recut.flagging.engine"):
                result = await _layer4_llm_judge([step], "prompt")

        assert result == []
        assert "local backend unreachable" in caplog.text

    async def test_rate_limit_retries_then_returns_empty(self, monkeypatch, caplog):
        import logging

        import anthropic

        monkeypatch.setenv("RECUT_L4_BACKEND", "anthropic")
        step = _make_step(0)

        with patch(
            "recut.flagging.engine._call_l4_api",
            side_effect=anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429),
                body={},
            ),
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with caplog.at_level(logging.WARNING, logger="recut.flagging.engine"):
                    result = await _layer4_llm_judge([step], "prompt")

        assert result == []
        assert "rate-limited" in caplog.text

    async def test_auth_error_returns_empty_with_warning(self, monkeypatch, caplog):
        import logging

        import anthropic

        monkeypatch.setenv("RECUT_L4_BACKEND", "anthropic")
        step = _make_step(0)

        with patch(
            "recut.flagging.engine._call_l4_api",
            side_effect=anthropic.AuthenticationError(
                message="auth error",
                response=MagicMock(status_code=401),
                body={},
            ),
        ):
            with caplog.at_level(logging.WARNING, logger="recut.flagging.engine"):
                result = await _layer4_llm_judge([step], "prompt")

        assert result == []
        assert "auth error" in caplog.text


# ===========================================================================
# Issue #19 — @recut.on_flag filters and hooks registry
# ===========================================================================


class TestMatchesFilter:
    def test_empty_filters_always_match(self):
        event = _make_flag_event(severity=Severity.LOW, flag_type=FlagType.GOAL_DRIFT)
        assert matches(event, {}) is True

    def test_none_filters_always_match(self):
        event = _make_flag_event(severity=Severity.HIGH, flag_type=FlagType.OVERCONFIDENCE)
        assert matches(event, {"severity": None, "flag_type": None}) is True

    def test_severity_filter_matches_correct_severity(self):
        event = _make_flag_event(severity=Severity.HIGH)
        assert matches(event, {"severity": "high"}) is True

    def test_severity_filter_rejects_wrong_severity(self):
        event = _make_flag_event(severity=Severity.LOW)
        assert matches(event, {"severity": "high"}) is False

    def test_flag_type_filter_matches_correct_type(self):
        event = _make_flag_event(flag_type=FlagType.OVERCONFIDENCE)
        assert matches(event, {"flag_type": "overconfidence"}) is True

    def test_flag_type_filter_rejects_wrong_type(self):
        event = _make_flag_event(flag_type=FlagType.GOAL_DRIFT)
        assert matches(event, {"flag_type": "overconfidence"}) is False

    def test_both_filters_must_match(self):
        event = _make_flag_event(severity=Severity.HIGH, flag_type=FlagType.OVERCONFIDENCE)
        assert matches(event, {"severity": "high", "flag_type": "overconfidence"}) is True

    def test_both_filters_fails_if_one_mismatches(self):
        event = _make_flag_event(severity=Severity.MEDIUM, flag_type=FlagType.OVERCONFIDENCE)
        assert matches(event, {"severity": "high", "flag_type": "overconfidence"}) is False


class TestRegisterAndGetAll:
    def test_register_adds_handler_to_registry(self):
        def handler(event):
            pass

        register(handler)
        all_handlers = get_all()
        assert len(all_handlers) == 1
        assert all_handlers[0][0] is handler

    def test_register_stores_filters(self):
        def handler(event):
            pass

        register(handler, severity="high", flag_type="overconfidence")
        _, filters = get_all()[0]
        assert filters["severity"] == "high"
        assert filters["flag_type"] == "overconfidence"

    def test_get_all_returns_copy(self):
        """Mutating the returned list should not affect the registry."""

        def handler(event):
            pass

        register(handler)
        result = get_all()
        result.clear()
        assert len(get_all()) == 1


class TestFireAll:
    async def test_fire_all_calls_sync_handler(self):
        calls = []

        def handler(event):
            calls.append(event)

        register(handler)
        event = _make_flag_event()
        await fire_all(event)

        assert len(calls) == 1
        assert calls[0] is event

    async def test_fire_all_calls_async_handler(self):
        calls = []

        async def handler(event):
            calls.append(event)

        register(handler)
        event = _make_flag_event()
        await fire_all(event)

        assert len(calls) == 1

    async def test_fire_all_skips_handler_when_filter_doesnt_match(self):
        calls = []

        def handler(event):
            calls.append(event)

        register(handler, severity="high")
        event = _make_flag_event(severity=Severity.LOW)
        await fire_all(event)

        assert calls == []

    async def test_fire_all_fires_only_matching_handlers(self):
        high_calls = []
        any_calls = []

        def high_handler(event):
            high_calls.append(event)

        def any_handler(event):
            any_calls.append(event)

        register(high_handler, severity="high")
        register(any_handler)

        event = _make_flag_event(severity=Severity.LOW)
        await fire_all(event)

        assert high_calls == []
        assert len(any_calls) == 1

    async def test_fire_all_swallows_handler_exceptions(self, caplog):
        import logging

        def bad_handler(event):
            raise RuntimeError("boom")

        register(bad_handler)
        event = _make_flag_event()

        with caplog.at_level(logging.WARNING, logger="recut.hooks"):
            await fire_all(event)  # should not raise

        assert "boom" in caplog.text


class TestOnFlagDecorator:
    def test_bare_decorator_registers_handler(self):
        @recut.on_flag
        def handler(event):
            pass

        assert len(get_all()) == 1

    def test_decorator_with_severity_kwarg(self):
        @recut.on_flag(severity="high")
        def handler(event):
            pass

        _, filters = get_all()[0]
        assert filters["severity"] == "high"
        assert filters["flag_type"] is None

    def test_decorator_with_flag_type_kwarg(self):
        @recut.on_flag(flag_type="overconfidence")
        def handler(event):
            pass

        _, filters = get_all()[0]
        assert filters["flag_type"] == "overconfidence"
        assert filters["severity"] is None

    def test_decorator_with_both_kwargs(self):
        @recut.on_flag(severity="high", flag_type="overconfidence")
        def handler(event):
            pass

        _, filters = get_all()[0]
        assert filters["severity"] == "high"
        assert filters["flag_type"] == "overconfidence"

    def test_decorator_returns_original_function(self):
        def handler(event):
            return "result"

        wrapped = recut.on_flag(handler)
        assert wrapped is handler


# ===========================================================================
# Issue #19 — handlers wired to all modes (peek, audit, intercept)
# ===========================================================================


class TestHandlersFiredInAllModes:
    """Verify that global on_flag handlers fire during peek, audit, and intercept."""

    def _pre_flagged_trace(self) -> RecutTrace:
        """A trace whose step will be flagged by layer 1 (repeated tool call)."""
        tool_content = '{"name": "search", "input": {"q": "test"}}'
        steps = [
            _make_step(0, StepType.TOOL_CALL, tool_content, step_id="step-0"),
            _make_step(1, StepType.TOOL_CALL, tool_content, step_id="step-1"),  # identical → flag
        ]
        return _make_trace(*steps)

    async def test_audit_fires_global_handler(self):
        from recut.core.auditor import audit

        calls = []
        register(lambda e: calls.append(e))

        # Disable embeddings and LLM judge so only layer 1 runs
        with patch.dict("os.environ", {"RECUT_USE_EMBEDDINGS": "false"}):
            await audit(self._pre_flagged_trace(), flagging_depth="fast")

        assert len(calls) >= 1

    async def test_peek_fires_global_handler(self):
        from recut.core.auditor import peek

        calls = []
        register(lambda e: calls.append(e))

        with patch.dict("os.environ", {"RECUT_USE_EMBEDDINGS": "false"}):
            await peek(self._pre_flagged_trace(), flagging_depth="fast")

        assert len(calls) >= 1

    async def test_intercept_fires_global_handler(self):
        from recut.core.interceptor import intercept

        calls = []
        register(lambda e: calls.append(e))

        trace = _make_trace()

        async def _gen() -> AsyncIterator[RecutStep]:
            # Two identical tool calls -> layer 1 flags the second
            tool_content = '{"name": "search", "input": {"q": "x"}}'
            yield _make_step(0, StepType.TOOL_CALL, tool_content)
            yield _make_step(1, StepType.TOOL_CALL, tool_content)

        with patch.dict("os.environ", {"RECUT_USE_EMBEDDINGS": "false"}):
            async for _ in intercept(trace, _gen()):
                pass

        assert len(calls) >= 1

    async def test_severity_filter_respected_in_audit(self):
        from recut.core.auditor import audit

        high_calls = []
        register(lambda e: high_calls.append(e), severity="low")  # only LOW
        # The repeated tool call flag is HIGH — should NOT fire
        register(lambda e: None, severity="high")

        with patch.dict("os.environ", {"RECUT_USE_EMBEDDINGS": "false"}):
            await audit(self._pre_flagged_trace(), flagging_depth="fast")

        # The HIGH-severity filter should not populate high_calls
        assert all(e.flag.severity.value == "low" for e in high_calls)
