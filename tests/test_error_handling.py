"""Tests for error handling and resilience improvements."""

from __future__ import annotations

import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recut.flagging.engine import _layer4_llm_judge, _parse_llm_flags
from recut.schema.trace import FlagSource, RecutFlag, RecutStep, StepType


def _make_step(content: str = "hello", index: int = 0) -> RecutStep:
    return RecutStep(index=index, type=StepType.OUTPUT, content=content)


def _flags_payload(step_id: str, flag_type: str = "overconfidence", score: float = 0.9) -> str:
    """Build a valid per-step flags JSON payload (dev's structured format)."""
    return json.dumps(
        [
            {
                "step_id": step_id,
                "flags": [
                    {
                        "flag_type": flag_type,
                        "score": score,
                        "plain_reason": "Test reason.",
                        "confidence": 0.8,
                        "evidence": "Some evidence.",
                    }
                ],
            }
        ]
    )


# ---------------------------------------------------------------------------
# _parse_llm_flags — structured per-step format
# ---------------------------------------------------------------------------


class TestParseLlmFlags:
    def test_valid_response_returns_flags(self):
        step = _make_step()
        raw = _flags_payload(step.id)
        flags = _parse_llm_flags(raw, [step])
        assert len(flags) == 1
        assert flags[0].source == FlagSource.LLM
        assert flags[0].plain_reason == "Test reason."
        assert flags[0].confidence == 0.8
        assert flags[0].evidence == "Some evidence."

    def test_invalid_json_returns_empty_and_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _parse_llm_flags("not json", [])
        assert result == []
        assert "non-JSON" in caplog.text

    def test_non_array_returns_empty(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _parse_llm_flags('{"step_id": "x"}', [])
        assert result == []
        assert "not a JSON array" in caplog.text

    def test_score_below_threshold_filtered(self):
        step = _make_step()
        raw = _flags_payload(step.id, score=0.1)
        flags = _parse_llm_flags(raw, [step])
        assert flags == []

    def test_unknown_flag_type_skipped(self):
        step = _make_step()
        raw = json.dumps(
            [{"step_id": step.id, "flags": [{"flag_type": "not_real", "score": 0.9}]}]
        )
        flags = _parse_llm_flags(raw, [step])
        assert flags == []

    def test_evidence_clipped_to_200_chars(self):
        step = _make_step()
        long_evidence = "x" * 300
        raw = json.dumps(
            [
                {
                    "step_id": step.id,
                    "flags": [
                        {
                            "flag_type": "overconfidence",
                            "score": 0.9,
                            "plain_reason": "r",
                            "evidence": long_evidence,
                        }
                    ],
                }
            ]
        )
        flags = _parse_llm_flags(raw, [step])
        assert len(flags) == 1
        assert len(flags[0].evidence) == 200

    def test_empty_evidence_becomes_none(self):
        step = _make_step()
        raw = json.dumps(
            [
                {
                    "step_id": step.id,
                    "flags": [
                        {
                            "flag_type": "overconfidence",
                            "score": 0.9,
                            "plain_reason": "r",
                            "evidence": "",
                        }
                    ],
                }
            ]
        )
        flags = _parse_llm_flags(raw, [step])
        assert flags[0].evidence is None

    def test_confidence_clamped_to_bounds(self):
        step = _make_step()
        raw = json.dumps(
            [
                {
                    "step_id": step.id,
                    "flags": [
                        {
                            "flag_type": "overconfidence",
                            "score": 0.9,
                            "plain_reason": "r",
                            "confidence": 1.5,
                        }
                    ],
                }
            ]
        )
        flags = _parse_llm_flags(raw, [step])
        assert flags[0].confidence == 1.0


# ---------------------------------------------------------------------------
# Layer 4 LLM judge — error handling (patches _call_l4_api)
# ---------------------------------------------------------------------------


class TestLayer4ErrorHandling:
    @pytest.mark.asyncio
    async def test_rate_limit_retries_then_returns_empty(self, caplog):
        import anthropic

        with (
            patch.dict(os.environ, {"RECUT_L4_BACKEND": "anthropic"}),
            patch(
                "recut.flagging.engine._call_l4_api",
                side_effect=anthropic.RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429, headers={}),
                    body={},
                ),
            ) as mock_call,
            caplog.at_level(logging.WARNING),
        ):
            result = await _layer4_llm_judge([_make_step()], "test prompt")

        assert result == []
        assert mock_call.call_count == 3
        assert "rate-limited" in caplog.text

    @pytest.mark.asyncio
    async def test_non_json_response_logs_warning_and_returns_empty(self, caplog):
        with (
            patch.dict(os.environ, {"RECUT_L4_BACKEND": "anthropic"}),
            patch(
                "recut.flagging.engine._call_l4_api",
                new_callable=AsyncMock,
                return_value="this is not json",
            ),
            caplog.at_level(logging.WARNING),
        ):
            result = await _layer4_llm_judge([_make_step()], "test prompt")

        assert result == []
        assert "non-JSON" in caplog.text

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self):
        with (
            patch.dict(os.environ, {"RECUT_L4_BACKEND": "anthropic"}),
            patch(
                "recut.flagging.engine._call_l4_api",
                new_callable=AsyncMock,
                return_value="",
            ),
        ):
            result = await _layer4_llm_judge([_make_step()], "prompt")
        assert result == []

    @pytest.mark.asyncio
    async def test_connection_error_on_anthropic_retries_then_skips(self, caplog):
        import anthropic

        with (
            patch.dict(os.environ, {"RECUT_L4_BACKEND": "anthropic"}),
            patch(
                "recut.flagging.engine._call_l4_api",
                side_effect=anthropic.APIConnectionError(request=MagicMock()),
            ) as mock_call,
            caplog.at_level(logging.WARNING),
        ):
            result = await _layer4_llm_judge([_make_step()], "prompt")

        assert result == []
        assert mock_call.call_count == 3
        assert "connection error" in caplog.text

    @pytest.mark.asyncio
    async def test_local_connection_error_silently_skips(self, caplog):
        """Local backend unreachable → immediate silent skip, no retries."""
        import anthropic

        with (
            patch.dict(os.environ, {"RECUT_L4_BACKEND": "local"}),
            patch(
                "recut.flagging.engine._call_l4_api",
                side_effect=anthropic.APIConnectionError(request=MagicMock()),
            ) as mock_call,
            caplog.at_level(logging.DEBUG),
        ):
            result = await _layer4_llm_judge([_make_step()], "prompt")

        assert result == []
        assert mock_call.call_count == 1  # no retries for local


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
# score_batch used in audit / peek
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
# L4 client cache (replaces old singleton test)
# ---------------------------------------------------------------------------


class TestL4ClientCache:
    def test_get_l4_client_returns_same_instance_per_backend(self):
        import recut.flagging.engine as eng

        eng._l4_clients.clear()
        with (
            patch.dict(os.environ, {"RECUT_L4_BACKEND": "anthropic"}),
            patch("anthropic.AsyncAnthropic") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance

            c1 = eng._get_l4_client("anthropic")
            c2 = eng._get_l4_client("anthropic")

        assert c1 is c2
        mock_cls.assert_called_once()
        eng._l4_clients.clear()
