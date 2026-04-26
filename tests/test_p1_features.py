"""Tests for P1 features: #22 flag attribution labels, #23 token costs, #24 structured L4 output."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from recut.flagging.engine import _parse_llm_flags
from recut.providers._pricing import ANTHROPIC_PRICING, OPENAI_PRICING, format_cost, resolve_cost
from recut.schema.trace import (
    FlagSource,
    FlagType,
    RecutFlag,
    RecutStep,
    Severity,
    StepType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(index: int = 0, content: str = "hello") -> RecutStep:
    return RecutStep(index=index, type=StepType.OUTPUT, content=content)


def _flag(
    flag_type: FlagType = FlagType.OVERCONFIDENCE,
    severity: Severity = Severity.MEDIUM,
    source: FlagSource = FlagSource.LLM,
    confidence: float | None = None,
    evidence: str | None = None,
) -> RecutFlag:
    return RecutFlag(
        type=flag_type,
        severity=severity,
        plain_reason="test reason",
        step_id="step-1",
        source=source,
        confidence=confidence,
        evidence=evidence,
    )


# ===========================================================================
# #24 — Structured Layer 4 judge output
# ===========================================================================


class TestRecutFlagSchema:
    def test_flag_has_confidence_and_evidence_fields(self) -> None:
        flag = _flag(confidence=0.85, evidence="agent said X")
        assert flag.confidence == 0.85
        assert flag.evidence == "agent said X"

    def test_flag_defaults_are_none(self) -> None:
        flag = _flag()
        assert flag.confidence is None
        assert flag.evidence is None

    def test_flag_serialises_none_fields(self) -> None:
        data = _flag().model_dump()
        assert "confidence" in data
        assert data["confidence"] is None
        assert data["evidence"] is None

    def test_flag_round_trips_via_json(self) -> None:
        original = _flag(confidence=0.9, evidence="some text")
        restored = RecutFlag(**original.model_dump())
        assert restored.confidence == original.confidence
        assert restored.evidence == original.evidence


class TestParseLlmFlags:
    def _make_raw(self, step_id: str, flags: list[dict]) -> str:
        return json.dumps([{"step_id": step_id, "flags": flags}])

    def test_parses_structured_flag_array(self) -> None:
        steps = [_step()]
        raw = self._make_raw(
            steps[0].id,
            [
                {
                    "flag_type": "overconfidence",
                    "score": 0.9,  # >= 0.85 threshold → HIGH
                    "confidence": 0.9,
                    "evidence": "agent stated certainty",
                    "plain_reason": "The agent was overconfident.",
                }
            ],
        )
        flags = _parse_llm_flags(raw, steps)
        assert len(flags) == 1
        assert flags[0].type == FlagType.OVERCONFIDENCE
        assert flags[0].severity == Severity.HIGH
        assert flags[0].confidence == pytest.approx(0.9)
        assert flags[0].evidence == "agent stated certainty"
        assert flags[0].source == FlagSource.LLM

    def test_filters_scores_below_threshold(self) -> None:
        steps = [_step()]
        raw = self._make_raw(
            steps[0].id,
            [{"flag_type": "goal_drift", "score": 0.1, "confidence": 0.5, "plain_reason": "x"}],
        )
        assert _parse_llm_flags(raw, steps) == []

    def test_ignores_unknown_flag_types(self) -> None:
        steps = [_step()]
        raw = self._make_raw(
            steps[0].id,
            [{"flag_type": "nonexistent_type", "score": 0.9, "plain_reason": "y"}],
        )
        assert _parse_llm_flags(raw, steps) == []

    def test_returns_empty_on_invalid_json(self) -> None:
        assert _parse_llm_flags("not json", [_step()]) == []

    def test_returns_empty_on_non_array(self) -> None:
        assert _parse_llm_flags('{"step_id": "x"}', [_step()]) == []

    def test_clips_evidence_to_200_chars(self) -> None:
        long_evidence = "x" * 300
        steps = [_step()]
        raw = self._make_raw(
            steps[0].id,
            [
                {
                    "flag_type": "overconfidence",
                    "score": 0.7,
                    "evidence": long_evidence,
                    "plain_reason": "test",
                }
            ],
        )
        flags = _parse_llm_flags(raw, steps)
        assert len(flags) == 1
        assert len(flags[0].evidence) <= 200  # type: ignore[arg-type]

    def test_confidence_clamped_to_0_1(self) -> None:
        steps = [_step()]
        raw = self._make_raw(
            steps[0].id,
            [{"flag_type": "overconfidence", "score": 0.7, "confidence": 1.5, "plain_reason": "x"}],
        )
        flags = _parse_llm_flags(raw, steps)
        assert flags[0].confidence == pytest.approx(1.0)

    def test_empty_evidence_string_becomes_none(self) -> None:
        steps = [_step()]
        raw = self._make_raw(
            steps[0].id,
            [{"flag_type": "overconfidence", "score": 0.7, "evidence": "", "plain_reason": "x"}],
        )
        flags = _parse_llm_flags(raw, steps)
        assert flags[0].evidence is None

    def test_multiple_steps_multiple_flags(self) -> None:
        step_a = _step(index=0, content="step a")
        step_b = _step(index=1, content="step b")
        raw = json.dumps(
            [
                {
                    "step_id": step_a.id,
                    "flags": [{"flag_type": "overconfidence", "score": 0.8, "plain_reason": "a"}],
                },
                {
                    "step_id": step_b.id,
                    "flags": [
                        {"flag_type": "goal_drift", "score": 0.6, "plain_reason": "b"},
                        {"flag_type": "scope_creep", "score": 0.5, "plain_reason": "c"},
                    ],
                },
            ]
        )
        flags = _parse_llm_flags(raw, [step_a, step_b])
        assert len(flags) == 3
        step_ids = [f.step_id for f in flags]
        assert step_a.id in step_ids
        assert step_b.id in step_ids

    def test_severity_mapping(self) -> None:
        steps = [_step()]
        cases = [
            (0.50, Severity.LOW),  # >= 0.4 but < 0.65
            (0.70, Severity.MEDIUM),  # >= 0.65 but < 0.85
            (0.90, Severity.HIGH),  # >= 0.85
        ]
        for score, expected_severity in cases:
            raw = self._make_raw(
                steps[0].id,
                [{"flag_type": "overconfidence", "score": score, "plain_reason": "x"}],
            )
            flags = _parse_llm_flags(raw, steps)
            assert flags[0].severity == expected_severity, f"score={score}"

    def test_fallback_plain_reason_when_missing(self) -> None:
        steps = [_step()]
        raw = self._make_raw(
            steps[0].id,
            [{"flag_type": "overconfidence", "score": 0.7}],
        )
        flags = _parse_llm_flags(raw, steps)
        assert len(flags) == 1
        assert "0.70" in flags[0].plain_reason


# ===========================================================================
# #22 — Flag attribution labels
# ===========================================================================


class TestFlagSourceLabels:
    """Verify FlagSource values match what CLI label maps expect."""

    def test_all_flag_sources_have_expected_values(self) -> None:
        assert FlagSource.RULE == "rule"
        assert FlagSource.EMBEDDING == "embedding"
        assert FlagSource.NATIVE == "native"
        assert FlagSource.LLM == "llm"

    def test_cli_label_map_covers_all_sources(self) -> None:
        from recut.cli.commands.peek_cmd import _SOURCE_LABEL

        for source in FlagSource:
            assert source in _SOURCE_LABEL, f"Missing label for {source}"

    def test_tui_label_map_covers_all_sources(self) -> None:
        from recut.cli.tui.audit_view import _SOURCE_LABEL as TUI_LABELS

        for source in FlagSource:
            assert source in TUI_LABELS, f"Missing TUI label for {source}"

    def test_rule_flags_display_rule_label(self) -> None:
        from recut.cli.commands.peek_cmd import _SOURCE_LABEL

        label = _SOURCE_LABEL[FlagSource.RULE]
        assert "rule" in label

    def test_native_flags_are_visually_distinct(self) -> None:
        from recut.cli.commands.peek_cmd import _SOURCE_LABEL

        native_label = _SOURCE_LABEL[FlagSource.NATIVE]
        rule_label = _SOURCE_LABEL[FlagSource.RULE]
        assert native_label != rule_label

    def test_judge_flags_display_judge_label(self) -> None:
        from recut.cli.commands.peek_cmd import _SOURCE_LABEL

        label = _SOURCE_LABEL[FlagSource.LLM]
        assert "judge" in label


# ===========================================================================
# #23 — Token cost tracking
# ===========================================================================


class TestResolveCost:
    def test_anthropic_known_model(self) -> None:
        # claude-sonnet-4-6: $3/M input, $15/M output
        cost = resolve_cost(ANTHROPIC_PRICING, "claude-sonnet-4-6", 1_000_000, 0)
        assert cost == pytest.approx(3.0)

    def test_anthropic_output_tokens(self) -> None:
        cost = resolve_cost(ANTHROPIC_PRICING, "claude-sonnet-4-6", 0, 1_000_000)
        assert cost == pytest.approx(15.0)

    def test_anthropic_mixed_tokens(self) -> None:
        cost = resolve_cost(ANTHROPIC_PRICING, "claude-haiku-4-5-20251001", 500_000, 500_000)
        # 0.5M * 0.80 + 0.5M * 4.0 = 0.40 + 2.00 = 2.40
        assert cost == pytest.approx(2.40)

    def test_anthropic_unknown_model_returns_none(self) -> None:
        assert resolve_cost(ANTHROPIC_PRICING, "claude-unknown-99", 100_000, 100_000) is None

    def test_zero_tokens(self) -> None:
        assert resolve_cost(ANTHROPIC_PRICING, "claude-sonnet-4-6", 0, 0) == pytest.approx(0.0)

    def test_openai_gpt4o_cost(self) -> None:
        cost = resolve_cost(OPENAI_PRICING, "gpt-4o", 1_000_000, 0)
        assert cost == pytest.approx(2.50)

    def test_openai_gpt4o_mini_cost(self) -> None:
        cost = resolve_cost(OPENAI_PRICING, "gpt-4o-mini", 0, 1_000_000)
        assert cost == pytest.approx(0.60)

    def test_openai_strips_date_suffix(self) -> None:
        cost_base = resolve_cost(OPENAI_PRICING, "gpt-4o", 100_000, 50_000)
        cost_dated = resolve_cost(
            OPENAI_PRICING, "gpt-4o-2024-11-20", 100_000, 50_000, strip_date_suffix=True
        )
        assert cost_base == pytest.approx(cost_dated)

    def test_openai_unknown_model_returns_none(self) -> None:
        assert resolve_cost(OPENAI_PRICING, "gpt-99-ultra", 100_000, 100_000) is None

    def test_env_var_override_replaces_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECUT_PRICE_INPUT", "1.0")
        monkeypatch.setenv("RECUT_PRICE_OUTPUT", "2.0")
        # Even for an unknown model, env overrides kick in
        cost = resolve_cost(ANTHROPIC_PRICING, "claude-unknown-99", 1_000_000, 1_000_000)
        # 1M * 1.0 + 1M * 2.0 = 3.0
        assert cost == pytest.approx(3.0)

    def test_env_var_override_with_known_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECUT_PRICE_INPUT", "0.5")
        monkeypatch.setenv("RECUT_PRICE_OUTPUT", "0.5")
        # Discounted rate overrides the table value of $3/$15 for sonnet
        cost = resolve_cost(ANTHROPIC_PRICING, "claude-sonnet-4-6", 1_000_000, 0)
        assert cost == pytest.approx(0.5)

    def test_env_var_invalid_value_falls_back_to_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RECUT_PRICE_INPUT", "not-a-number")
        monkeypatch.setenv("RECUT_PRICE_OUTPUT", "not-a-number")
        cost = resolve_cost(ANTHROPIC_PRICING, "claude-sonnet-4-6", 1_000_000, 0)
        assert cost == pytest.approx(3.0)  # falls back to table


class TestFormatCost:
    def test_usd_default_uses_dollar_sign(self) -> None:
        assert format_cost(0.0042) == "$0.0042"

    def test_custom_unit_appended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECUT_COST_UNIT", "EUR")
        assert format_cost(0.0042) == "0.0042 EUR"

    def test_credits_unit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECUT_COST_UNIT", "credits")
        result = format_cost(1.5)
        assert "1.5000" in result
        assert "credits" in result


class TestTracerCostAggregation:
    def test_finalize_aggregates_step_costs(self) -> None:
        from recut.core.tracer import RecutContext
        from recut.providers.anthropic import AnthropicProvider
        from recut.schema.trace import RecutTrace, TraceLanguage, TraceMeta, TraceMode

        provider = MagicMock(spec=AnthropicProvider)
        provider.model = "claude-sonnet-4-6"
        provider.__class__.__name__ = "AnthropicProvider"

        trace = RecutTrace(
            agent_id="test",
            prompt="test",
            mode=TraceMode.AUDIT,
            language=TraceLanguage.SIMPLE,
            meta=TraceMeta(model="claude-sonnet-4-6", provider="AnthropicProvider"),
        )
        ctx = RecutContext(trace=trace, provider=provider, flag_handlers=[])

        step_a = RecutStep(index=0, type=StepType.OUTPUT, content="a", token_cost=0.001)
        step_b = RecutStep(index=1, type=StepType.OUTPUT, content="b", token_cost=0.002)
        ctx.add_step(step_a)
        ctx.add_step(step_b)

        ctx.finalize()
        assert trace.meta.token_cost == pytest.approx(0.003, rel=1e-5)

    def test_finalize_skips_cost_when_no_steps_have_cost(self) -> None:
        from recut.core.tracer import RecutContext
        from recut.schema.trace import RecutTrace, TraceLanguage, TraceMeta, TraceMode

        provider = MagicMock()
        provider.model = "claude-sonnet-4-6"
        provider.__class__.__name__ = "AnthropicProvider"

        trace = RecutTrace(
            agent_id="test",
            prompt="test",
            mode=TraceMode.AUDIT,
            language=TraceLanguage.SIMPLE,
            meta=TraceMeta(model="claude-sonnet-4-6", provider="AnthropicProvider"),
        )
        ctx = RecutContext(trace=trace, provider=provider, flag_handlers=[])
        ctx.add_step(RecutStep(index=0, type=StepType.OUTPUT, content="a"))
        ctx.finalize()
        assert trace.meta.token_cost is None

    def test_finalize_aggregates_token_counts(self) -> None:
        from recut.core.tracer import RecutContext
        from recut.schema.trace import RecutTrace, TraceLanguage, TraceMeta, TraceMode

        provider = MagicMock()
        provider.model = "claude-sonnet-4-6"
        provider.__class__.__name__ = "AnthropicProvider"

        trace = RecutTrace(
            agent_id="test",
            prompt="test",
            mode=TraceMode.AUDIT,
            language=TraceLanguage.SIMPLE,
            meta=TraceMeta(model="claude-sonnet-4-6", provider="AnthropicProvider"),
        )
        ctx = RecutContext(trace=trace, provider=provider, flag_handlers=[])
        ctx.add_step(RecutStep(index=0, type=StepType.OUTPUT, content="a", token_count=500))
        ctx.add_step(RecutStep(index=1, type=StepType.OUTPUT, content="b", token_count=300))
        ctx.finalize()
        assert trace.meta.token_count == 800


class TestAnthropicProviderCost:
    """Integration-style tests for cost propagation through run_agent."""

    @pytest.mark.asyncio
    async def test_run_agent_sets_token_cost_on_output_step(self) -> None:
        from recut.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.model = "claude-sonnet-4-6"
        provider.thinking_budget = 10_000

        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 500

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello world"

        mock_response = MagicMock()
        mock_response.content = [text_block]
        mock_response.usage = mock_usage

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        steps = [s async for s in provider.run_agent("test")]
        assert len(steps) == 1
        # 1000 * 3 / 1M + 500 * 15 / 1M = 0.003 + 0.0075 = 0.0105
        assert steps[0].token_cost == pytest.approx(0.0105, rel=1e-4)
        assert steps[0].token_count == 1500

    @pytest.mark.asyncio
    async def test_run_agent_unknown_model_cost_is_none(self) -> None:
        from recut.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.model = "claude-unknown-future"
        provider.thinking_budget = 10_000

        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 500

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello"

        mock_response = MagicMock()
        mock_response.content = [text_block]
        mock_response.usage = mock_usage

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        steps = [s async for s in provider.run_agent("test")]
        assert steps[0].token_cost is None


class TestOpenAIProviderCost:
    @pytest.mark.asyncio
    async def test_run_agent_sets_token_cost_on_output_step(self) -> None:
        from recut.providers.openai import OpenAIProvider

        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "gpt-4o"
        provider.infer_reasoning = False

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 1000
        mock_usage.completion_tokens = 500

        mock_message = MagicMock()
        mock_message.content = "Hello"
        mock_message.tool_calls = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        steps = [s async for s in provider.run_agent("test")]
        assert len(steps) == 1
        # 1000 * 2.50 / 1M + 500 * 10.0 / 1M = 0.0025 + 0.005 = 0.0075
        assert steps[0].token_cost == pytest.approx(0.0075, rel=1e-4)
        assert steps[0].token_count == 1500
