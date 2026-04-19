"""
Tests for the flagging engine in recut/flagging/engine.py.
No live API calls — only layers 1 and 3 are tested here.
Layer 4 (LLM judge) is never invoked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from recut.flagging.engine import (
    FlaggingEngine,
    _cache_key,
    _layer1_rules,
    _layer3_native_mismatch,
)
from recut.schema.trace import (
    FlagSource,
    FlagType,
    ReasoningSource,
    RecutFlag,
    RecutStep,
    Severity,
    StepReasoning,
    StepType,
    TraceMode,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(
    index: int,
    step_type: StepType,
    content: str,
    *,
    reasoning_content: str | None = None,
    reasoning_source: ReasoningSource = ReasoningSource.NATIVE,
    step_id: str | None = None,
) -> RecutStep:
    reasoning = None
    if reasoning_content is not None:
        reasoning = StepReasoning(
            source=reasoning_source,
            content=reasoning_content,
            confidence=0.8,
        )
    return RecutStep(
        id=step_id or f"step-{index}",
        index=index,
        type=step_type,
        content=content,
        reasoning=reasoning,
    )


# ===========================================================================
# Layer 1 — Rule-based
# ===========================================================================


class TestLayer1Rules:
    def test_repeated_identical_tool_call_flags_high(self):
        """Repeated tool call with identical content -> anomalous_tool_use HIGH."""
        content = '{"name": "search", "input": {"query": "news"}}'
        preceding = [_make_step(0, StepType.TOOL_CALL, content)]
        step = _make_step(1, StepType.TOOL_CALL, content)

        flags = _layer1_rules(step, preceding)

        assert len(flags) >= 1
        repeated_flags = [
            f
            for f in flags
            if f.type == FlagType.ANOMALOUS_TOOL_USE and f.severity == Severity.HIGH
        ]
        assert len(repeated_flags) == 1
        assert repeated_flags[0].source == FlagSource.RULE
        assert repeated_flags[0].step_id == step.id

    def test_different_tool_calls_no_repeated_flag(self):
        """Two tool calls with different content should not raise repetition flag."""
        preceding = [_make_step(0, StepType.TOOL_CALL, '{"name": "search", "query": "foo"}')]
        step = _make_step(1, StepType.TOOL_CALL, '{"name": "search", "query": "bar"}')

        flags = _layer1_rules(step, preceding)

        repeated_flags = [
            f
            for f in flags
            if f.type == FlagType.ANOMALOUS_TOOL_USE and f.severity == Severity.HIGH
        ]
        assert len(repeated_flags) == 0

    def test_tool_call_with_no_preceding_reasoning_flags_low(self):
        """Tool call with no reasoning attached and no preceding reasoning step -> anomalous_tool_use LOW."""
        preceding = [_make_step(0, StepType.TOOL_RESULT, "some result")]
        step = _make_step(1, StepType.TOOL_CALL, '{"name": "run"}')
        # step.reasoning is None by default

        flags = _layer1_rules(step, preceding)

        low_flags = [
            f for f in flags if f.type == FlagType.ANOMALOUS_TOOL_USE and f.severity == Severity.LOW
        ]
        assert len(low_flags) == 1

    def test_tool_call_preceded_by_reasoning_step_no_low_flag(self):
        """A tool call preceded by a reasoning step should not fire the LOW no-reasoning flag."""
        preceding = [_make_step(0, StepType.REASONING, "Let me think...")]
        step = _make_step(1, StepType.TOOL_CALL, '{"name": "run"}')

        flags = _layer1_rules(step, preceding)

        low_no_reasoning = [
            f for f in flags if f.type == FlagType.ANOMALOUS_TOOL_USE and f.severity == Severity.LOW
        ]
        assert len(low_no_reasoning) == 0

    def test_empty_reasoning_block_on_tool_call_flags_medium(self):
        """Tool call with reasoning attached but empty content -> reasoning_gap MEDIUM."""
        step = _make_step(
            1,
            StepType.TOOL_CALL,
            '{"name": "run"}',
            reasoning_content="   ",  # whitespace only
        )

        flags = _layer1_rules(step, [])

        gap_flags = [
            f for f in flags if f.type == FlagType.REASONING_GAP and f.severity == Severity.MEDIUM
        ]
        assert len(gap_flags) == 1
        assert gap_flags[0].source == FlagSource.RULE

    def test_empty_reasoning_block_on_output_flags_medium(self):
        """Output with empty reasoning block -> reasoning_gap MEDIUM."""
        step = _make_step(
            2,
            StepType.OUTPUT,
            "Here is the answer.",
            reasoning_content="",
        )

        flags = _layer1_rules(step, [])

        gap_flags = [f for f in flags if f.type == FlagType.REASONING_GAP]
        assert len(gap_flags) == 1
        assert gap_flags[0].severity == Severity.MEDIUM

    def test_step_index_above_20_flags_scope_creep_low(self):
        """Step index > 20 -> scope_creep LOW."""
        step = _make_step(21, StepType.OUTPUT, "Still going...")

        flags = _layer1_rules(step, [])

        creep_flags = [f for f in flags if f.type == FlagType.SCOPE_CREEP]
        assert len(creep_flags) == 1
        assert creep_flags[0].severity == Severity.LOW

    def test_step_index_exactly_20_no_scope_creep(self):
        """Step index == 20 should not fire scope_creep (boundary: must be > 20)."""
        step = _make_step(20, StepType.OUTPUT, "Edge case.")

        flags = _layer1_rules(step, [])

        creep_flags = [f for f in flags if f.type == FlagType.SCOPE_CREEP]
        assert len(creep_flags) == 0

    def test_normal_step_no_issues_returns_empty(self):
        """A clean reasoning step with proper context should return no flags."""
        preceding = [_make_step(0, StepType.REASONING, "Let me think through this.")]
        step = _make_step(
            1, StepType.OUTPUT, "The answer is Paris.", reasoning_content="Paris is the capital."
        )

        flags = _layer1_rules(step, preceding)

        assert flags == []

    def test_reasoning_step_never_triggers_reasoning_gap(self):
        """REASONING type steps with empty content should NOT trigger reasoning_gap."""
        step = _make_step(0, StepType.REASONING, "thinking...", reasoning_content="")

        flags = _layer1_rules(step, [])

        gap_flags = [f for f in flags if f.type == FlagType.REASONING_GAP]
        assert len(gap_flags) == 0


# ===========================================================================
# Layer 3 — Native reasoning/action mismatch
# ===========================================================================


class TestLayer3NativeMismatch:
    def test_uncertainty_in_thinking_confidence_in_output_flags_high(self):
        """Native reasoning with uncertainty phrases + output with confidence phrases -> HIGH mismatch."""
        step = _make_step(
            3,
            StepType.OUTPUT,
            "The answer is definitely correct. I know that this is the right path.",
            reasoning_content="I'm not sure which approach to take. I might be wrong about this.",
        )

        flag = _layer3_native_mismatch(step)

        assert flag is not None
        assert flag.type == FlagType.REASONING_ACTION_MISMATCH
        assert flag.severity == Severity.HIGH
        assert flag.source == FlagSource.NATIVE
        assert flag.step_id == step.id

    def test_no_uncertainty_in_thinking_no_flag(self):
        """Native reasoning without uncertainty phrases -> no flag regardless of output."""
        step = _make_step(
            4,
            StepType.OUTPUT,
            "The answer is definitely correct.",
            reasoning_content="I have analyzed all the options and this is the best approach.",
        )

        flag = _layer3_native_mismatch(step)

        assert flag is None

    def test_uncertainty_in_thinking_but_no_confidence_in_output_no_flag(self):
        """Uncertainty in reasoning but no confidence phrases in output -> no flag."""
        step = _make_step(
            5,
            StepType.OUTPUT,
            "Here is what I found based on available information.",
            reasoning_content="I'm not sure which source to use here.",
        )

        flag = _layer3_native_mismatch(step)

        assert flag is None

    def test_inferred_reasoning_source_no_flag(self):
        """Layer 3 only fires on NATIVE reasoning — inferred source should return None."""
        step = _make_step(
            6,
            StepType.OUTPUT,
            "I know that the answer is absolutely certain.",
            reasoning_content="I'm not sure and might be wrong about this.",
            reasoning_source=ReasoningSource.INFERRED,
        )

        flag = _layer3_native_mismatch(step)

        assert flag is None

    def test_none_reasoning_returns_none(self):
        """Step with no reasoning object -> returns None."""
        step = RecutStep(
            id="step-no-reasoning",
            index=7,
            type=StepType.OUTPUT,
            content="The answer is definitely correct.",
        )

        flag = _layer3_native_mismatch(step)

        assert flag is None

    def test_empty_reasoning_content_no_flag(self):
        """Empty reasoning content (no uncertainty phrases present) -> no flag."""
        step = _make_step(
            8,
            StepType.OUTPUT,
            "The answer is certainly true.",
            reasoning_content="",
        )

        flag = _layer3_native_mismatch(step)

        # Empty reasoning has no uncertainty phrases -> thinking_uncertain is False
        assert flag is None

    def test_each_uncertainty_phrase_triggers(self):
        """Spot-check that each uncertainty phrase can trigger the flag."""
        from recut.flagging.flags import CONFIDENCE_PHRASES, UNCERTAINTY_PHRASES

        # Use first confidence phrase for the output
        conf_phrase = CONFIDENCE_PHRASES[0]

        for phrase in UNCERTAINTY_PHRASES[:5]:  # test first 5 to keep it fast
            step = _make_step(
                9,
                StepType.OUTPUT,
                f"{conf_phrase} this is the answer.",
                reasoning_content=f"I {phrase} about the best approach.",
            )
            flag = _layer3_native_mismatch(step)
            assert flag is not None, f"Expected flag for uncertainty phrase: '{phrase}'"

    def test_each_confidence_phrase_triggers(self):
        """Spot-check that each confidence phrase in output can trigger the flag."""
        from recut.flagging.flags import CONFIDENCE_PHRASES, UNCERTAINTY_PHRASES

        uncert_phrase = UNCERTAINTY_PHRASES[0]  # "not sure"

        for phrase in CONFIDENCE_PHRASES[:5]:  # test first 5
            step = _make_step(
                10,
                StepType.OUTPUT,
                f"The response: {phrase}.",
                reasoning_content=f"I am {uncert_phrase} about this.",
            )
            flag = _layer3_native_mismatch(step)
            assert flag is not None, f"Expected flag for confidence phrase: '{phrase}'"


# ===========================================================================
# Flag caching
# ===========================================================================


class TestFlagCaching:
    async def test_same_content_twice_second_call_returns_cached(self):
        """
        When _get_cached_flags returns a non-None result, score_step should
        return it immediately without re-running layers.
        """
        step = _make_step(0, StepType.OUTPUT, "Repeated content.")
        preceding: list[RecutStep] = []
        _cache_key(step, preceding)

        cached_flag = RecutFlag(
            type=FlagType.GOAL_DRIFT,
            severity=Severity.MEDIUM,
            plain_reason="Cached.",
            step_id=step.id,
            source=FlagSource.RULE,
        )

        engine = FlaggingEngine(mode=TraceMode.PEEK, use_embeddings=False, use_llm_judge=False)

        with (
            patch(
                "recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=[cached_flag])
            ) as mock_get,
            patch("recut.flagging.engine._cache_flags", new=AsyncMock()) as mock_set,
        ):
            result = await engine.score_step(step, preceding, "original prompt")

        assert result == [cached_flag]
        mock_get.assert_called_once()
        # _cache_flags should NOT be called when a cache hit occurs
        mock_set.assert_not_called()

    async def test_cache_miss_then_result_is_cached(self):
        """
        When _get_cached_flags returns None, flags are computed and then cached.
        """
        content = '{"name": "run"}'
        preceding_with_tool = [_make_step(0, StepType.TOOL_CALL, content)]
        step = _make_step(1, StepType.TOOL_CALL, content)  # repeated -> HIGH flag

        engine = FlaggingEngine(mode=TraceMode.PEEK, use_embeddings=False, use_llm_judge=False)

        with (
            patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)),
            patch("recut.flagging.engine._cache_flags", new=AsyncMock()) as mock_set,
        ):
            result = await engine.score_step(step, preceding_with_tool, "prompt")

        # Should have flagged the repeated tool call
        assert any(
            f.type == FlagType.ANOMALOUS_TOOL_USE and f.severity == Severity.HIGH for f in result
        )
        # Cache should be written
        mock_set.assert_called_once()


# ===========================================================================
# FlaggingEngine integration (layers 1+3 only)
# ===========================================================================


class TestFlaggingEngineIntegration:
    async def test_score_step_layer1_repeated_tool_call(self):
        """FlaggingEngine.score_step fires layer 1 for repeated tool call."""
        content = '{"name": "fetch", "url": "http://example.com"}'
        preceding = [_make_step(0, StepType.TOOL_CALL, content)]
        step = _make_step(1, StepType.TOOL_CALL, content)

        engine = FlaggingEngine(mode=TraceMode.PEEK, use_embeddings=False, use_llm_judge=False)

        with (
            patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)),
            patch("recut.flagging.engine._cache_flags", new=AsyncMock()),
        ):
            flags = await engine.score_step(step, preceding, "do something")

        assert any(
            f.type == FlagType.ANOMALOUS_TOOL_USE and f.severity == Severity.HIGH for f in flags
        )

    async def test_score_step_layer3_mismatch(self):
        """FlaggingEngine.score_step fires layer 3 when native mismatch is present."""
        step = _make_step(
            2,
            StepType.OUTPUT,
            "The answer is definitely and certainly right.",
            reasoning_content="I'm not sure and I might be wrong.",
        )

        engine = FlaggingEngine(mode=TraceMode.PEEK, use_embeddings=False, use_llm_judge=False)

        with (
            patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)),
            patch("recut.flagging.engine._cache_flags", new=AsyncMock()),
        ):
            flags = await engine.score_step(step, [], "original")

        assert any(f.type == FlagType.REASONING_ACTION_MISMATCH for f in flags)

    async def test_score_step_clean_step_no_flags(self):
        """A clean step returns no flags when layers 2 and 4 are disabled."""
        step = _make_step(
            0,
            StepType.OUTPUT,
            "The capital of France is Paris.",
            reasoning_content="This is a simple geography question.",
        )

        engine = FlaggingEngine(mode=TraceMode.PEEK, use_embeddings=False, use_llm_judge=False)

        with (
            patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)),
            patch("recut.flagging.engine._cache_flags", new=AsyncMock()),
        ):
            flags = await engine.score_step(step, [], "What is the capital of France?")

        assert flags == []

    async def test_score_batch_returns_dict_keyed_by_step_id(self):
        """score_batch returns a dict mapping step_id -> list of flags."""
        content = '{"name": "search"}'
        steps = [
            _make_step(0, StepType.TOOL_CALL, content),
            _make_step(1, StepType.TOOL_CALL, content),  # repeated
        ]

        engine = FlaggingEngine(mode=TraceMode.PEEK, use_embeddings=False, use_llm_judge=False)

        with (
            patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)),
            patch("recut.flagging.engine._cache_flags", new=AsyncMock()),
        ):
            results = await engine.score_batch(steps, "search for something")

        # step 0 may get a LOW no-preceding-reasoning flag
        # step 1 should get the HIGH repeated tool call flag
        assert steps[1].id in results
        high_flags = [
            f
            for f in results[steps[1].id]
            if f.type == FlagType.ANOMALOUS_TOOL_USE and f.severity == Severity.HIGH
        ]
        assert len(high_flags) == 1
