"""
Shared pytest fixtures for the recut-ai test suite.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from recut.schema.fork import ForkInjection, InjectionTarget
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
    TraceMeta,
)

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture file loaders
# ---------------------------------------------------------------------------


@pytest.fixture
def trace_simple_json() -> dict[str, Any]:
    """Raw JSON dict from trace_simple.json."""
    return json.loads((FIXTURES_DIR / "trace_simple.json").read_text())


@pytest.fixture
def trace_with_flags_json() -> dict[str, Any]:
    """Raw JSON dict from trace_with_flags.json."""
    return json.loads((FIXTURES_DIR / "trace_with_flags.json").read_text())


@pytest.fixture
def trace_simple(trace_simple_json: dict[str, Any]) -> RecutTrace:
    """trace_simple.json loaded into a RecutTrace model."""
    return RecutTrace.model_validate(trace_simple_json)


@pytest.fixture
def trace_with_flags(trace_with_flags_json: dict[str, Any]) -> RecutTrace:
    """trace_with_flags.json loaded into a RecutTrace model."""
    return RecutTrace.model_validate(trace_with_flags_json)


# ---------------------------------------------------------------------------
# Step builders
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_reasoning_step() -> RecutStep:
    return RecutStep(
        id="srs-001",
        index=0,
        type=StepType.REASONING,
        content="I should check the user's request carefully.",
        reasoning=StepReasoning(
            source=ReasoningSource.NATIVE,
            content="I should check the user's request carefully.",
            thinking_tokens=30,
            confidence=0.9,
        ),
    )


@pytest.fixture
def sample_tool_call_step() -> RecutStep:
    return RecutStep(
        id="stc-001",
        index=1,
        type=StepType.TOOL_CALL,
        content='{"name": "search", "input": {"query": "example"}}',
    )


@pytest.fixture
def sample_output_step() -> RecutStep:
    return RecutStep(
        id="sout-001",
        index=2,
        type=StepType.OUTPUT,
        content="The answer is 42.",
    )


@pytest.fixture
def sample_flag() -> RecutFlag:
    return RecutFlag(
        type=FlagType.ANOMALOUS_TOOL_USE,
        severity=Severity.HIGH,
        plain_reason="Repeated identical tool call detected.",
        step_id="stc-001",
        source=FlagSource.RULE,
    )


# ---------------------------------------------------------------------------
# Injection / Fork helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_injection() -> ForkInjection:
    return ForkInjection(
        target=InjectionTarget.TOOL_RESULT,
        original_content="Original tool result",
        injected_content="Injected tool result",
    )


# ---------------------------------------------------------------------------
# Trace meta helper
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_meta() -> TraceMeta:
    return TraceMeta(
        model="claude-sonnet-4-6",
        provider="AnthropicProvider",
        duration_seconds=2.5,
        total_steps=3,
        token_count=200,
        thinking_tokens=80,
    )
