"""
Tests for all Pydantic schema models in recut/schema/.
All tests are offline — no API calls.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from recut.schema.audit import AuditMode, AuditRecord, ReviewStatus, RiskProfile
from recut.schema.fork import (
    ForkDiff,
    ForkInjection,
    ForkType,
    InjectionTarget,
    RecutFork,
)
from recut.schema.hooks import RecutFlagEvent
from recut.schema.stress import InjectionStrategy, RecutStressRun, StressVerdict
from recut.schema.trace import (
    FlagSource,
    FlagType,
    ReasoningSource,
    RecutFlag,
    RecutStep,
    RecutTrace,
    Severity,
    StepReasoning,
    StepType,
    TraceLanguage,
    TraceMeta,
    TraceMode,
)

# ===========================================================================
# RecutFlag
# ===========================================================================


class TestRecutFlag:
    def test_construction_all_fields(self):
        flag = RecutFlag(
            type=FlagType.ANOMALOUS_TOOL_USE,
            severity=Severity.HIGH,
            plain_reason="Loop detected.",
            step_id="step-001",
            source=FlagSource.RULE,
        )
        assert flag.type == FlagType.ANOMALOUS_TOOL_USE
        assert flag.severity == Severity.HIGH
        assert flag.plain_reason == "Loop detected."
        assert flag.step_id == "step-001"
        assert flag.source == FlagSource.RULE

    def test_serialization_round_trip(self):
        flag = RecutFlag(
            type=FlagType.REASONING_GAP,
            severity=Severity.MEDIUM,
            plain_reason="Empty reasoning.",
            step_id="s-2",
            source=FlagSource.EMBEDDING,
        )
        dumped = flag.model_dump(mode="json")
        restored = RecutFlag.model_validate(dumped)
        assert restored == flag

    def test_json_round_trip(self):
        flag = RecutFlag(
            type=FlagType.GOAL_DRIFT,
            severity=Severity.LOW,
            plain_reason="Drifted.",
            step_id="s-3",
            source=FlagSource.LLM,
        )
        raw = flag.model_dump_json()
        restored = RecutFlag.model_validate_json(raw)
        assert restored.type == FlagType.GOAL_DRIFT
        assert restored.source == FlagSource.LLM


# ===========================================================================
# StepReasoning
# ===========================================================================


class TestStepReasoning:
    def test_construction_with_all_fields(self):
        sr = StepReasoning(
            source=ReasoningSource.NATIVE,
            content="I think the answer is 42.",
            thinking_tokens=80,
            confidence=0.95,
        )
        assert sr.source == ReasoningSource.NATIVE
        assert sr.thinking_tokens == 80
        assert sr.confidence == 0.95

    def test_optional_thinking_tokens_defaults_none(self):
        sr = StepReasoning(
            source=ReasoningSource.INFERRED,
            content="Inferred content.",
            confidence=0.5,
        )
        assert sr.thinking_tokens is None

    def test_confidence_boundary_values(self):
        sr_min = StepReasoning(source=ReasoningSource.NATIVE, content="x", confidence=0.0)
        sr_max = StepReasoning(source=ReasoningSource.NATIVE, content="x", confidence=1.0)
        assert sr_min.confidence == 0.0
        assert sr_max.confidence == 1.0

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            StepReasoning(source=ReasoningSource.NATIVE, content="x", confidence=1.5)

    def test_serialization_round_trip(self):
        sr = StepReasoning(
            source=ReasoningSource.INFERRED,
            content="meta reasoning",
            thinking_tokens=None,
            confidence=0.7,
        )
        restored = StepReasoning.model_validate(sr.model_dump(mode="json"))
        assert restored == sr


# ===========================================================================
# RecutStep
# ===========================================================================


class TestRecutStep:
    def test_construction_defaults(self):
        step = RecutStep(index=0, type=StepType.OUTPUT, content="Hello.")
        assert step.risk_score == 0.0
        assert step.flags == []
        assert step.plain_summary == ""
        assert step.fork_eligible is True
        assert step.reasoning is None
        assert len(step.id) == 36  # UUID4

    def test_construction_with_all_fields(self, sample_reasoning_step):
        s = sample_reasoning_step
        assert s.index == 0
        assert s.type == StepType.REASONING
        assert s.reasoning is not None
        assert s.reasoning.source == ReasoningSource.NATIVE

    def test_risk_score_boundaries(self):
        step = RecutStep(index=0, type=StepType.OUTPUT, content="x", risk_score=1.0)
        assert step.risk_score == 1.0
        with pytest.raises(ValidationError):
            RecutStep(index=0, type=StepType.OUTPUT, content="x", risk_score=1.1)

    def test_serialization_round_trip(self, sample_tool_call_step):
        step = sample_tool_call_step
        dumped = step.model_dump(mode="json")
        restored = RecutStep.model_validate(dumped)
        assert restored.id == step.id
        assert restored.type == step.type
        assert restored.content == step.content

    def test_with_flags(self, sample_flag):
        step = RecutStep(
            index=3,
            type=StepType.TOOL_CALL,
            content="tool call",
            flags=[sample_flag],
            risk_score=1.0,
        )
        assert len(step.flags) == 1
        assert step.flags[0].severity == Severity.HIGH


# ===========================================================================
# TraceMeta
# ===========================================================================


class TestTraceMeta:
    def test_defaults(self):
        meta = TraceMeta(model="claude-haiku", provider="AnthropicProvider")
        assert meta.duration_seconds is None
        assert meta.total_steps == 0
        assert meta.token_count is None
        assert meta.thinking_tokens is None

    def test_all_fields(self, sample_meta):
        assert sample_meta.model == "claude-sonnet-4-6"
        assert sample_meta.total_steps == 3
        assert sample_meta.duration_seconds == 2.5


# ===========================================================================
# RecutTrace
# ===========================================================================


class TestRecutTrace:
    def test_construction_defaults(self):
        trace = RecutTrace(
            agent_id="agent-1",
            prompt="Hello",
            mode=TraceMode.PEEK,
            meta=TraceMeta(model="m", provider="p"),
        )
        assert trace.steps == []
        assert trace.language == TraceLanguage.SIMPLE
        assert len(trace.id) == 36
        assert isinstance(trace.created_at, datetime)

    def test_with_steps(self, sample_reasoning_step, sample_output_step, sample_meta):
        trace = RecutTrace(
            agent_id="agent-2",
            prompt="Run something",
            mode=TraceMode.AUDIT,
            language=TraceLanguage.POWER,
            meta=sample_meta,
            steps=[sample_reasoning_step, sample_output_step],
        )
        assert len(trace.steps) == 2
        assert trace.language == TraceLanguage.POWER

    def test_serialization_round_trip(self, trace_simple):
        dumped = trace_simple.model_dump(mode="json")
        restored = RecutTrace.model_validate(dumped)
        assert restored.id == trace_simple.id
        assert restored.agent_id == trace_simple.agent_id
        assert len(restored.steps) == len(trace_simple.steps)

    def test_json_round_trip(self, trace_simple):
        raw = trace_simple.model_dump_json()
        restored = RecutTrace.model_validate_json(raw)
        assert restored.prompt == trace_simple.prompt

    def test_load_trace_simple_fixture(self, trace_simple):
        assert trace_simple.id == "fixture-trace-001"
        assert trace_simple.agent_id == "test-agent"
        assert trace_simple.prompt == "What is the capital of France?"
        assert trace_simple.mode == TraceMode.PEEK
        assert trace_simple.language == TraceLanguage.SIMPLE
        assert len(trace_simple.steps) == 2
        assert trace_simple.meta.model == "claude-sonnet-4-6"
        assert trace_simple.meta.total_steps == 3

    def test_load_trace_with_flags_fixture(self, trace_with_flags):
        assert trace_with_flags.id == "fixture-trace-002"
        assert trace_with_flags.mode == TraceMode.AUDIT
        assert len(trace_with_flags.steps) == 4
        # Verify the repeated tool call steps exist
        tool_calls = [s for s in trace_with_flags.steps if s.type == StepType.TOOL_CALL]
        assert len(tool_calls) == 2
        assert tool_calls[0].content == tool_calls[1].content

    def test_trace_simple_step_reasoning(self, trace_simple):
        step0 = trace_simple.steps[0]
        assert step0.type == StepType.REASONING
        assert step0.reasoning is not None
        assert step0.reasoning.source == ReasoningSource.NATIVE
        assert step0.reasoning.confidence == 1.0

    def test_trace_with_flags_output_step(self, trace_with_flags):
        output_step = trace_with_flags.steps[3]
        assert output_step.type == StepType.OUTPUT
        assert "definitely" in output_step.content
        assert output_step.reasoning is not None
        assert output_step.reasoning.source == ReasoningSource.NATIVE


# ===========================================================================
# RecutFork, ForkInjection, ForkDiff
# ===========================================================================


class TestForkInjection:
    def test_construction(self):
        inj = ForkInjection(
            target=InjectionTarget.TOOL_RESULT,
            original_content="orig",
            injected_content="injected",
        )
        assert inj.target == InjectionTarget.TOOL_RESULT
        assert inj.original_content == "orig"
        assert inj.injected_content == "injected"

    def test_all_injection_targets(self):
        for target in InjectionTarget:
            inj = ForkInjection(
                target=target,
                original_content="a",
                injected_content="b",
            )
            assert inj.target == target


class TestForkDiff:
    def test_construction(self):
        diff = ForkDiff(
            divergence_step=5,
            plain_summary="Diverged at step 5.",
            risk_delta=0.25,
        )
        assert diff.divergence_step == 5
        assert diff.risk_delta == 0.25

    def test_negative_risk_delta(self):
        diff = ForkDiff(divergence_step=2, plain_summary="Less risky.", risk_delta=-0.15)
        assert diff.risk_delta == -0.15


class TestRecutFork:
    def test_construction_defaults(self, sample_injection):
        fork = RecutFork(
            parent_trace_id="trace-123",
            fork_step_index=2,
            injection=sample_injection,
        )
        assert fork.fork_type == ForkType.MANUAL
        assert fork.replay_steps == []
        assert fork.diff is None
        assert len(fork.id) == 36
        assert isinstance(fork.created_at, datetime)

    def test_construction_all_fields(self, sample_injection):
        diff = ForkDiff(divergence_step=2, plain_summary="Changed.", risk_delta=0.1)
        fork = RecutFork(
            parent_trace_id="trace-456",
            fork_step_index=3,
            fork_type=ForkType.STRESS_VARIANT,
            injection=sample_injection,
            replay_steps=[{"index": 3}],
            diff=diff,
        )
        assert fork.fork_type == ForkType.STRESS_VARIANT
        assert len(fork.replay_steps) == 1
        assert fork.diff is not None

    def test_serialization_round_trip(self, sample_injection):
        fork = RecutFork(
            parent_trace_id="t-1",
            fork_step_index=0,
            injection=sample_injection,
        )
        dumped = fork.model_dump(mode="json")
        restored = RecutFork.model_validate(dumped)
        assert restored.id == fork.id
        assert restored.fork_type == ForkType.MANUAL


# ===========================================================================
# AuditRecord, RiskProfile
# ===========================================================================


class TestRiskProfile:
    def test_all_defaults_zero(self):
        profile = RiskProfile()
        assert profile.overconfidence_count == 0
        assert profile.goal_drift_count == 0
        assert profile.scope_creep_count == 0
        assert profile.reasoning_gap_count == 0
        assert profile.uncertainty_suppression_count == 0
        assert profile.instruction_deviation_count == 0
        assert profile.anomalous_tool_use_count == 0
        assert profile.reasoning_action_mismatch_count == 0

    def test_explicit_counts(self):
        profile = RiskProfile(anomalous_tool_use_count=3, reasoning_gap_count=1)
        assert profile.anomalous_tool_use_count == 3
        assert profile.reasoning_gap_count == 1

    def test_serialization_round_trip(self):
        profile = RiskProfile(overconfidence_count=2, goal_drift_count=1)
        restored = RiskProfile.model_validate(profile.model_dump(mode="json"))
        assert restored == profile


class TestAuditRecord:
    def test_construction_defaults(self):
        record = AuditRecord(
            trace_id="trace-001",
            mode=AuditMode.PEEK,
            behavioral_summary="All clear.",
        )
        assert record.flag_count == 0
        assert record.highest_severity is None
        assert record.review_status == ReviewStatus.AUTO
        assert record.fork_ids == []
        assert record.review_notes is None
        assert record.reviewer is None
        assert record.exported_at is None
        assert isinstance(record.created_at, datetime)
        assert len(record.id) == 36

    def test_construction_all_fields(self):
        profile = RiskProfile(anomalous_tool_use_count=1)
        record = AuditRecord(
            trace_id="trace-002",
            mode=AuditMode.AUDIT,
            behavioral_summary="Issues found.",
            flag_count=2,
            highest_severity="high",
            risk_profile=profile,
            review_status=ReviewStatus.PENDING_HUMAN,
            review_notes="Needs review.",
            reviewer="alice",
        )
        assert record.flag_count == 2
        assert record.highest_severity == "high"
        assert record.risk_profile.anomalous_tool_use_count == 1
        assert record.reviewer == "alice"

    def test_serialization_round_trip(self):
        record = AuditRecord(
            trace_id="trace-003",
            mode=AuditMode.PEEK,
            behavioral_summary="Ok.",
            flag_count=1,
        )
        restored = AuditRecord.model_validate(record.model_dump(mode="json"))
        assert restored.trace_id == record.trace_id
        assert restored.flag_count == 1


# ===========================================================================
# RecutStressRun
# ===========================================================================


class TestRecutStressRun:
    def test_construction(self):
        run = RecutStressRun(
            parent_trace_id="trace-001",
            source_flag_type=FlagType.ANOMALOUS_TOOL_USE.value,
            variant_index=0,
            injection_strategy=InjectionStrategy.AMPLIFY_UNCERTAINTY,
            fork_id="fork-001",
            verdict=StressVerdict.STABLE,
            plain_summary="Agent remained stable.",
            risk_delta=0.05,
        )
        assert run.parent_trace_id == "trace-001"
        assert run.verdict == StressVerdict.STABLE
        assert run.injection_strategy == InjectionStrategy.AMPLIFY_UNCERTAINTY
        assert len(run.id) == 36

    def test_all_verdicts(self):
        for verdict in StressVerdict:
            run = RecutStressRun(
                parent_trace_id="t",
                source_flag_type="anomalous_tool_use",
                variant_index=0,
                injection_strategy=InjectionStrategy.ADVERSARIAL_INPUT,
                fork_id="f",
                verdict=verdict,
                plain_summary=".",
                risk_delta=0.0,
            )
            assert run.verdict == verdict

    def test_all_injection_strategies(self):
        for strategy in InjectionStrategy:
            run = RecutStressRun(
                parent_trace_id="t",
                source_flag_type="scope_creep",
                variant_index=1,
                injection_strategy=strategy,
                fork_id="f",
                verdict=StressVerdict.DEGRADED,
                plain_summary=".",
                risk_delta=0.2,
            )
            assert run.injection_strategy == strategy


# ===========================================================================
# RecutFlagEvent
# ===========================================================================


class TestRecutFlagEvent:
    def test_construction_with_flag_handler(self, sample_flag, sample_tool_call_step):
        event = RecutFlagEvent(
            trace_id="trace-001",
            step_id="stc-001",
            flag=sample_flag,
            suggested_action="escalate",
            preceding_steps=[sample_tool_call_step],
            agent_id="agent-1",
        )
        assert event.trace_id == "trace-001"
        assert event.flag.type == FlagType.ANOMALOUS_TOOL_USE
        assert event.suggested_action == "escalate"
        assert len(event.preceding_steps) == 1

    def test_defaults(self, sample_flag):
        event = RecutFlagEvent(
            trace_id="t",
            step_id="s",
            flag=sample_flag,
            suggested_action="peek",
            agent_id="a",
        )
        assert event.preceding_steps == []

    def test_serialization_round_trip(self, sample_flag):
        event = RecutFlagEvent(
            trace_id="t",
            step_id="s",
            flag=sample_flag,
            suggested_action="audit",
            agent_id="agent-x",
        )
        dumped = event.model_dump(mode="json")
        restored = RecutFlagEvent.model_validate(dumped)
        assert restored.trace_id == "t"
        assert restored.flag.severity == Severity.HIGH


# ===========================================================================
# Enum round-trips
# ===========================================================================


class TestEnumRoundTrips:
    """All str enums in schema/trace.py should serialize to str and back."""

    def test_step_type_round_trip(self):
        for member in StepType:
            assert StepType(member.value) == member
            assert isinstance(member.value, str)

    def test_reasoning_source_round_trip(self):
        for member in ReasoningSource:
            assert ReasoningSource(member.value) == member

    def test_flag_type_round_trip(self):
        for member in FlagType:
            assert FlagType(member.value) == member

    def test_flag_source_round_trip(self):
        for member in FlagSource:
            assert FlagSource(member.value) == member

    def test_severity_round_trip(self):
        for member in Severity:
            assert Severity(member.value) == member

    def test_trace_mode_round_trip(self):
        for member in TraceMode:
            assert TraceMode(member.value) == member

    def test_trace_language_round_trip(self):
        for member in TraceLanguage:
            assert TraceLanguage(member.value) == member

    def test_enums_serialize_to_string_in_json(self):
        flag = RecutFlag(
            type=FlagType.SCOPE_CREEP,
            severity=Severity.LOW,
            plain_reason="Too many steps.",
            step_id="s-99",
            source=FlagSource.RULE,
        )
        dumped = json.loads(flag.model_dump_json())
        assert dumped["type"] == "scope_creep"
        assert dumped["severity"] == "low"
        assert dumped["source"] == "rule"

    def test_trace_enums_in_json(self):
        trace = RecutTrace(
            agent_id="a",
            prompt="p",
            mode=TraceMode.STRESS,
            language=TraceLanguage.POWER,
            meta=TraceMeta(model="m", provider="p"),
        )
        dumped = json.loads(trace.model_dump_json())
        assert dumped["mode"] == "stress"
        assert dumped["language"] == "power"
