"""
Tests for recut/core/replayer.py and recut/core/auditor.py.
No live API calls — providers and storage are mocked throughout.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from recut.core.auditor import (
    _build_risk_profile,
    _compute_risk_score,
    audit,
    peek,
)
from recut.core.replayer import _compute_diff
from recut.schema.audit import AuditMode, AuditRecord, RiskProfile
from recut.schema.fork import ForkDiff, ForkInjection, InjectionTarget
from recut.schema.trace import (
    FlagSource,
    FlagType,
    RecutFlag,
    RecutStep,
    RecutTrace,
    ReasoningSource,
    Severity,
    StepReasoning,
    StepType,
    TraceMeta,
    TraceLanguage,
    TraceMode,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step(
    index: int,
    content: str,
    risk_score: float = 0.0,
    step_type: StepType = StepType.OUTPUT,
    flags: list[RecutFlag] | None = None,
    reasoning_content: str | None = None,
    reasoning_source: ReasoningSource = ReasoningSource.NATIVE,
    step_id: str | None = None,
) -> RecutStep:
    reasoning = None
    if reasoning_content is not None:
        reasoning = StepReasoning(
            source=reasoning_source,
            content=reasoning_content,
            confidence=0.9,
        )
    return RecutStep(
        id=step_id or f"step-{index}",
        index=index,
        type=step_type,
        content=content,
        risk_score=risk_score,
        flags=flags or [],
        reasoning=reasoning,
    )


def _make_flag(flag_type: FlagType, severity: Severity, step_id: str = "s") -> RecutFlag:
    return RecutFlag(
        type=flag_type,
        severity=severity,
        plain_reason="Test flag.",
        step_id=step_id,
        source=FlagSource.RULE,
    )


# ===========================================================================
# _compute_diff
# ===========================================================================

class TestComputeDiff:

    def test_identical_steps_divergence_equals_fork_index(self):
        """When all steps are identical, divergence_step should equal fork_index."""
        fork_index = 3
        steps = [_make_step(i, f"identical content {i}") for i in range(3)]
        original = steps
        replayed = [_make_step(i, f"identical content {i}") for i in range(3)]

        diff = _compute_diff(original, replayed, fork_index)

        assert diff.divergence_step == fork_index

    def test_identical_steps_risk_delta_near_zero(self):
        """When steps are identical, risk_delta should be 0."""
        steps_orig = [_make_step(i, f"content {i}", risk_score=0.3) for i in range(3)]
        steps_rep = [_make_step(i, f"content {i}", risk_score=0.3) for i in range(3)]

        diff = _compute_diff(steps_orig, steps_rep, fork_index=0)

        assert abs(diff.risk_delta) < 0.01

    def test_diverging_steps_correct_divergence_step(self):
        """The first differing step should be identified correctly."""
        fork_index = 2
        original = [
            _make_step(0, "same content A"),
            _make_step(1, "same content B"),
            _make_step(2, "original content C"),  # diverges here
            _make_step(3, "original content D"),
        ]
        replayed = [
            _make_step(0, "same content A"),
            _make_step(1, "same content B"),
            _make_step(2, "DIFFERENT content C"),  # diverges here
            _make_step(3, "original content D"),
        ]

        diff = _compute_diff(original, replayed, fork_index)

        assert diff.divergence_step == fork_index + 2  # index 2 in the zipped list

    def test_first_step_diverges(self):
        """Divergence at the very first step."""
        original = [_make_step(0, "original")]
        replayed = [_make_step(0, "different")]

        diff = _compute_diff(original, replayed, fork_index=5)

        assert diff.divergence_step == 5  # fork_index + 0

    def test_higher_replay_risk_positive_delta_and_riskier_in_summary(self):
        """When replay risk > original risk by > 0.1, summary should mention 'riskier'."""
        original = [_make_step(0, "content", risk_score=0.2)]
        replayed = [_make_step(0, "content", risk_score=0.8)]

        diff = _compute_diff(original, replayed, fork_index=0)

        assert diff.risk_delta > 0.1
        assert "riskier" in diff.plain_summary.lower()

    def test_lower_replay_risk_negative_delta_and_cautious_in_summary(self):
        """When replay risk < original risk by > 0.1, summary should mention 'cautious'."""
        original = [_make_step(0, "content", risk_score=0.9)]
        replayed = [_make_step(0, "content", risk_score=0.1)]

        diff = _compute_diff(original, replayed, fork_index=0)

        assert diff.risk_delta < -0.1
        assert "cautious" in diff.plain_summary.lower()

    def test_similar_risk_summary_mentions_similar(self):
        """When risk_delta is within 0.1, summary should indicate minimal effect."""
        original = [_make_step(0, "content A", risk_score=0.4)]
        replayed = [_make_step(0, "content B", risk_score=0.45)]

        diff = _compute_diff(original, replayed, fork_index=0)

        assert abs(diff.risk_delta) < 0.1
        # Summary should mention "similar" or "little"
        summary_lower = diff.plain_summary.lower()
        assert "similar" in summary_lower or "little" in summary_lower

    def test_empty_step_lists_returns_fork_index(self):
        """Empty lists should return fork_index with zero delta."""
        diff = _compute_diff([], [], fork_index=7)

        assert diff.divergence_step == 7
        assert diff.risk_delta == 0.0

    def test_risk_delta_is_rounded(self):
        """risk_delta should be rounded to 3 decimal places."""
        original = [_make_step(0, "x", risk_score=0.1)]
        replayed = [_make_step(0, "x", risk_score=0.4)]

        diff = _compute_diff(original, replayed, fork_index=0)

        # Should be exactly 0.3 after rounding
        assert diff.risk_delta == round(0.4 - 0.1, 3)

    def test_returns_fork_diff_instance(self):
        """_compute_diff always returns a ForkDiff."""
        diff = _compute_diff([], [], fork_index=0)
        assert isinstance(diff, ForkDiff)


# ===========================================================================
# _compute_risk_score
# ===========================================================================

class TestComputeRiskScore:

    def test_empty_flags_returns_zero(self):
        assert _compute_risk_score([]) == 0.0

    def test_high_flag_returns_one(self):
        flags = [_make_flag(FlagType.ANOMALOUS_TOOL_USE, Severity.HIGH)]
        assert _compute_risk_score(flags) == 1.0

    def test_medium_flag_returns_point_six(self):
        flags = [_make_flag(FlagType.REASONING_GAP, Severity.MEDIUM)]
        assert _compute_risk_score(flags) == 0.6

    def test_low_flag_returns_point_three(self):
        flags = [_make_flag(FlagType.SCOPE_CREEP, Severity.LOW)]
        assert _compute_risk_score(flags) == 0.3

    def test_multiple_flags_returns_max(self):
        flags = [
            _make_flag(FlagType.SCOPE_CREEP, Severity.LOW),
            _make_flag(FlagType.REASONING_GAP, Severity.MEDIUM),
        ]
        assert _compute_risk_score(flags) == 0.6

    def test_mixed_with_high_returns_one(self):
        flags = [
            _make_flag(FlagType.SCOPE_CREEP, Severity.LOW),
            _make_flag(FlagType.ANOMALOUS_TOOL_USE, Severity.HIGH),
            _make_flag(FlagType.REASONING_GAP, Severity.MEDIUM),
        ]
        assert _compute_risk_score(flags) == 1.0


# ===========================================================================
# _build_risk_profile
# ===========================================================================

class TestBuildRiskProfile:

    def test_empty_flags_all_zero(self):
        profile = _build_risk_profile([])
        assert profile.anomalous_tool_use_count == 0
        assert profile.reasoning_action_mismatch_count == 0
        assert profile.scope_creep_count == 0

    def test_counts_each_flag_type(self):
        flags = [
            _make_flag(FlagType.ANOMALOUS_TOOL_USE, Severity.HIGH, "s1"),
            _make_flag(FlagType.ANOMALOUS_TOOL_USE, Severity.HIGH, "s2"),
            _make_flag(FlagType.REASONING_GAP, Severity.MEDIUM, "s3"),
            _make_flag(FlagType.SCOPE_CREEP, Severity.LOW, "s4"),
            _make_flag(FlagType.GOAL_DRIFT, Severity.MEDIUM, "s5"),
            _make_flag(FlagType.OVERCONFIDENCE, Severity.LOW, "s6"),
            _make_flag(FlagType.UNCERTAINTY_SUPPRESSION, Severity.LOW, "s7"),
            _make_flag(FlagType.INSTRUCTION_DEVIATION, Severity.MEDIUM, "s8"),
            _make_flag(FlagType.REASONING_ACTION_MISMATCH, Severity.HIGH, "s9"),
        ]

        profile = _build_risk_profile(flags)

        assert profile.anomalous_tool_use_count == 2
        assert profile.reasoning_gap_count == 1
        assert profile.scope_creep_count == 1
        assert profile.goal_drift_count == 1
        assert profile.overconfidence_count == 1
        assert profile.uncertainty_suppression_count == 1
        assert profile.instruction_deviation_count == 1
        assert profile.reasoning_action_mismatch_count == 1

    def test_returns_risk_profile_instance(self):
        result = _build_risk_profile([])
        assert isinstance(result, RiskProfile)


# ===========================================================================
# peek() — auditor
# ===========================================================================

class TestPeek:

    async def test_peek_returns_audit_record(self, trace_simple):
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await peek(trace_simple)

        assert isinstance(record, AuditRecord)
        assert record.trace_id == trace_simple.id
        assert record.mode == AuditMode.PEEK

    async def test_peek_clean_fixture_flag_count_zero(self, trace_simple):
        """trace_simple.json has no anomalies — peek should return flag_count=0."""
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await peek(trace_simple)

        assert record.flag_count == 0
        assert record.highest_severity is None

    async def test_peek_detects_anomalous_tool_use_in_flagged_fixture(self, trace_with_flags):
        """trace_with_flags.json has a repeated tool call — peek should flag it."""
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await peek(trace_with_flags)

        assert record.flag_count > 0
        assert record.risk_profile.anomalous_tool_use_count >= 1

    async def test_peek_detects_reasoning_action_mismatch_in_flagged_fixture(self, trace_with_flags):
        """
        trace_with_flags.json step-013 has uncertainty in native reasoning
        but confidence phrases in output — layer 3 should detect the mismatch.
        """
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await peek(trace_with_flags)

        assert record.risk_profile.reasoning_action_mismatch_count >= 1

    async def test_peek_highest_severity_set_on_flagged_trace(self, trace_with_flags):
        """A trace with HIGH flags should produce highest_severity='high'."""
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await peek(trace_with_flags)

        # We expect at least one HIGH flag from the repeated tool call
        assert record.highest_severity is not None

    async def test_peek_behavioral_summary_is_string(self, trace_simple):
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await peek(trace_simple)

        assert isinstance(record.behavioral_summary, str)
        assert len(record.behavioral_summary) > 0


# ===========================================================================
# audit() — auditor
# ===========================================================================

class TestAudit:

    async def test_audit_clean_fixture_flag_count_zero(self, trace_simple):
        """audit on clean trace should return AuditRecord with flag_count=0."""
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await audit(trace_simple)

        assert isinstance(record, AuditRecord)
        assert record.flag_count == 0
        assert record.mode == AuditMode.AUDIT

    async def test_audit_returns_audit_mode(self, trace_with_flags):
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await audit(trace_with_flags)

        assert record.mode == AuditMode.AUDIT

    async def test_audit_flagged_fixture_has_flags(self, trace_with_flags):
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await audit(trace_with_flags)

        assert record.flag_count > 0

    async def test_audit_review_status_pending_human_when_high_severity(self, trace_with_flags):
        """AuditRecord.review_status should be PENDING_HUMAN when there is a HIGH flag."""
        from recut.schema.audit import ReviewStatus

        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await audit(trace_with_flags)

        if record.highest_severity == "high":
            assert record.review_status == ReviewStatus.PENDING_HUMAN

    async def test_audit_trace_id_matches(self, trace_simple):
        with patch("recut.flagging.engine._get_cached_flags", new=AsyncMock(return_value=None)), \
             patch("recut.flagging.engine._cache_flags", new=AsyncMock()):
            record = await audit(trace_simple)

        assert record.trace_id == trace_simple.id
