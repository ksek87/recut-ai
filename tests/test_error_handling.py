"""Tests for error handling and resilience improvements."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recut.flagging.engine import _layer4_llm_judge, _meta_client, _parse_llm_flags
from recut.schema.trace import FlagSource, RecutFlag, RecutStep, StepType


def _make_step(content: str = "hello", index: int = 0) -> RecutStep:
    return RecutStep(index=index, type=StepType.OUTPUT, content=content)


# ---------------------------------------------------------------------------
# _parse_llm_flags
# ---------------------------------------------------------------------------


class TestParseLlmFlags:
    def test_valid_response_returns_flags(self):
        step = _make_step()
        raw = json.dumps(
            [
                {
                    "step_id": step.id,
                    "overconfidence": 0.9,
                    "plain_reasons": {"overconfidence": "Too sure of itself."},
                }
            ]
        )
        flags = _parse_llm_flags(raw, [step])
        assert len(flags) == 1
        assert flags[0].source == FlagSource.LLM
        assert flags[0].plain_reason == "Too sure of itself."

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_flags("not json", [])

    def test_non_array_returns_empty(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _parse_llm_flags('{"step_id": "x"}', [])
        assert result == []
        assert "not a JSON array" in caplog.text

    def test_score_below_threshold_filtered(self):
        step = _make_step()
        raw = json.dumps([{"step_id": step.id, "overconfidence": 0.1, "plain_reasons": {}}])
        flags = _parse_llm_flags(raw, [step])
        assert flags == []

    def test_unknown_flag_type_skipped(self):
        step = _make_step()
        raw = json.dumps([{"step_id": step.id, "not_a_real_flag": 0.9, "plain_reasons": {}}])
        flags = _parse_llm_flags(raw, [step])
        assert flags == []


# ---------------------------------------------------------------------------
# Layer 4 LLM judge — error handling
# ---------------------------------------------------------------------------


class TestLayer4ErrorHandling:
    @pytest.mark.asyncio
    async def test_rate_limit_retries_then_returns_empty(self, caplog):
        import anthropic

        with patch(
            "recut.flagging.engine._get_meta_client"
        ) as mock_client_fn, caplog.at_level(logging.WARNING):
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(
                side_effect=anthropic.RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429, headers={}),
                    body={},
                )
            )
            mock_client_fn.return_value = mock_client

            step = _make_step()
            result = await _layer4_llm_judge([step], "test prompt")

        assert result == []
        assert mock_client.messages.create.call_count == 3
        assert "rate-limited" in caplog.text

    @pytest.mark.asyncio
    async def test_non_json_response_logs_warning_and_returns_empty(self, caplog):
        mock_block = MagicMock()
        mock_block.text = "this is not json"
        mock_response = MagicMock()
        mock_response.content = [mock_block]

        with patch(
            "recut.flagging.engine._get_meta_client"
        ) as mock_client_fn, caplog.at_level(logging.WARNING):
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            step = _make_step()
            result = await _layer4_llm_judge([step], "test prompt")

        assert result == []
        assert "non-JSON" in caplog.text

    @pytest.mark.asyncio
    async def test_empty_response_content_returns_empty(self):
        mock_response = MagicMock()
        mock_response.content = []

        with patch("recut.flagging.engine._get_meta_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await _layer4_llm_judge([_make_step()], "prompt")

        assert result == []

    @pytest.mark.asyncio
    async def test_connection_error_retries_then_returns_empty(self, caplog):
        import anthropic

        with patch(
            "recut.flagging.engine._get_meta_client"
        ) as mock_client_fn, caplog.at_level(logging.WARNING):
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(
                side_effect=anthropic.APIConnectionError(request=MagicMock())
            )
            mock_client_fn.return_value = mock_client

            result = await _layer4_llm_judge([_make_step()], "prompt")

        assert result == []
        assert mock_client.messages.create.call_count == 3
        assert "connection error" in caplog.text


# ---------------------------------------------------------------------------
# Anthropic provider — error handling
# ---------------------------------------------------------------------------


class TestAnthropicProviderErrors:
    @pytest.mark.asyncio
    async def test_auth_error_raises_runtime_error(self):
        import anthropic

        from recut.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider()
        provider._client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="invalid key",
                response=MagicMock(status_code=401, headers={}),
                body={},
            )
        )

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            async for _ in provider.run_agent("test"):
                pass

    @pytest.mark.asyncio
    async def test_rate_limit_retries(self):
        import anthropic

        from recut.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider()
        mock_response = MagicMock()
        mock_response.content = []

        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise anthropic.RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429, headers={}),
                    body={},
                )
            return mock_response

        provider._client.messages.create = side_effect

        steps = [s async for s in provider.run_agent("test")]
        assert call_count == 3
        assert steps == []


# ---------------------------------------------------------------------------
# Stress — continues on variant failure
# ---------------------------------------------------------------------------


class TestStressContinuesOnFailure:
    @pytest.mark.asyncio
    async def test_failed_variant_does_not_block_others(self):
        from recut.core.stress import stress
        from recut.schema.trace import (
            FlagSource,
            FlagType,
            RecutFlag,
            RecutTrace,
            Severity,
            TraceMeta,
            TraceMode,
        )

        trace = RecutTrace(
            agent_id="test",
            prompt="test",
            mode=TraceMode.STRESS,
            meta=TraceMeta(model="x", provider="y"),
            steps=[],
        )
        # Give the step two flags so two variants are attempted
        step = _make_step("do something risky")
        step.risk_score = 0.9
        step.flags = [
            RecutFlag(
                type=FlagType.OVERCONFIDENCE,
                severity=Severity.HIGH,
                plain_reason="Too confident.",
                step_id=step.id,
                source=FlagSource.RULE,
            ),
            RecutFlag(
                type=FlagType.GOAL_DRIFT,
                severity=Severity.MEDIUM,
                plain_reason="Drifting.",
                step_id=step.id,
                source=FlagSource.RULE,
            ),
        ]
        trace.steps.append(step)

        call_count = 0

        async def mock_replay(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("provider failure")
            mock_fork = MagicMock()
            mock_fork.id = "fork-id"
            mock_fork.diff = MagicMock()
            mock_fork.diff.risk_delta = 0.1
            return mock_fork

        with patch("recut.core.stress.replay", side_effect=mock_replay):
            runs = await stress(trace, MagicMock(), num_variants=2)

        # First variant failed, second should still succeed
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_all_variants_fail_returns_empty(self):
        from recut.core.stress import stress
        from recut.schema.trace import (
            FlagSource,
            FlagType,
            RecutFlag,
            RecutTrace,
            Severity,
            TraceMeta,
            TraceMode,
        )

        trace = RecutTrace(
            agent_id="test",
            prompt="test",
            mode=TraceMode.STRESS,
            meta=TraceMeta(model="x", provider="y"),
            steps=[],
        )
        step = _make_step("risky step")
        step.risk_score = 0.9
        step.flags = [
            RecutFlag(
                type=FlagType.OVERCONFIDENCE,
                severity=Severity.HIGH,
                plain_reason="Too confident.",
                step_id=step.id,
                source=FlagSource.RULE,
            )
        ]
        trace.steps.append(step)

        with patch(
            "recut.core.stress.replay", side_effect=RuntimeError("always fails")
        ):
            runs = await stress(trace, MagicMock(), num_variants=2)

        assert runs == []


# ---------------------------------------------------------------------------
# score_batch used in audit
# ---------------------------------------------------------------------------


class TestScoreBatchUsedInAudit:
    @pytest.mark.asyncio
    async def test_audit_calls_score_batch_not_score_step(self):
        from recut.core.auditor import audit
        from recut.schema.trace import RecutTrace, TraceMeta, TraceMode

        trace = RecutTrace(
            agent_id="test",
            prompt="test",
            mode=TraceMode.AUDIT,
            meta=TraceMeta(model="x", provider="y"),
            steps=[_make_step()],
        )

        with patch(
            "recut.core.auditor.FlaggingEngine.score_batch", new_callable=AsyncMock
        ) as mock_batch, patch(
            "recut.core.auditor.FlaggingEngine.score_step", new_callable=AsyncMock
        ) as mock_step:
            mock_batch.return_value = {}
            await audit(trace)
            mock_batch.assert_called_once()
            mock_step.assert_not_called()

    @pytest.mark.asyncio
    async def test_peek_calls_score_batch_not_score_step(self):
        from recut.core.auditor import peek
        from recut.schema.trace import RecutTrace, TraceMeta, TraceMode

        trace = RecutTrace(
            agent_id="test",
            prompt="test",
            mode=TraceMode.PEEK,
            meta=TraceMeta(model="x", provider="y"),
            steps=[_make_step()],
        )

        with patch(
            "recut.core.auditor.FlaggingEngine.score_batch", new_callable=AsyncMock
        ) as mock_batch, patch(
            "recut.core.auditor.FlaggingEngine.score_step", new_callable=AsyncMock
        ) as mock_step:
            mock_batch.return_value = {}
            await peek(trace)
            mock_batch.assert_called_once()
            mock_step.assert_not_called()


# ---------------------------------------------------------------------------
# Meta-client singleton
# ---------------------------------------------------------------------------


class TestMetaClientSingleton:
    def test_get_meta_client_returns_same_instance(self):
        import recut.flagging.engine as eng

        eng._meta_client = None  # reset for test isolation
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance

            c1 = eng._get_meta_client()
            c2 = eng._get_meta_client()

        assert c1 is c2
        mock_cls.assert_called_once()
