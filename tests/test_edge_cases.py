"""
Edge-case and regression tests for bugs found in the codebase audit.
No live API calls — storage and providers are mocked throughout.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from recut.core.tracer import RecutContext, trace
from recut.schema.trace import (
    FlagSource,
    FlagType,
    RecutFlag,
    RecutStep,
    RecutTrace,
    Severity,
    StepType,
)

# ---------------------------------------------------------------------------
# Stub provider
# ---------------------------------------------------------------------------


class _StubProvider:
    model = "stub-model"

    async def capture_step(self, raw_response: dict) -> RecutStep:  # pragma: no cover
        raise NotImplementedError

    def supports_native_reasoning(self) -> bool:
        return False

    async def replay_from(  # pragma: no cover
        self, steps, fork_index, injection
    ) -> list[RecutStep]:
        raise NotImplementedError

    async def run_agent(self, prompt, system=None, tools=None):  # pragma: no cover
        raise NotImplementedError


def _make_flag(**kwargs) -> RecutFlag:
    defaults = dict(
        type=FlagType.ANOMALOUS_TOOL_USE,
        severity=Severity.HIGH,
        plain_reason="test flag",
        step_id="s-1",
        source=FlagSource.RULE,
    )
    defaults.update(kwargs)
    return RecutFlag(**defaults)


# ===========================================================================
# RecutFlag.confidence validation
# ===========================================================================


class TestRecutFlagConfidenceBounds:
    def test_valid_confidence_zero(self):
        f = _make_flag(confidence=0.0)
        assert f.confidence == 0.0

    def test_valid_confidence_one(self):
        f = _make_flag(confidence=1.0)
        assert f.confidence == 1.0

    def test_valid_confidence_mid(self):
        f = _make_flag(confidence=0.75)
        assert f.confidence == 0.75

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            _make_flag(confidence=1.1)

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            _make_flag(confidence=-0.1)

    def test_confidence_none_allowed(self):
        f = _make_flag(confidence=None)
        assert f.confidence is None


# ===========================================================================
# trace_if predicate exception handling
# ===========================================================================


class TestTraceIfExceptionHandling:
    async def test_trace_if_raising_predicate_function_still_runs(self):
        """If trace_if raises, the decorated function should still run."""
        calls = []

        def bad_predicate(ctx: RecutContext) -> bool:
            raise RuntimeError("predicate broken")

        @trace(agent_id="x", sample_rate=1.0, trace_if=bad_predicate, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            calls.append(prompt)
            return "ok"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
            result = await my_agent("hello")

        assert result == "ok"
        assert calls == ["hello"]
        mock_persist.assert_not_called()

    async def test_trace_if_raising_predicate_logs_warning(self, caplog):
        """Predicate exceptions should be logged at WARNING level."""

        def bad_predicate(ctx: RecutContext) -> bool:
            raise ValueError("bad predicate")

        @trace(agent_id="x", sample_rate=1.0, trace_if=bad_predicate, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            return "done"

        with caplog.at_level(logging.WARNING, logger="recut.core.tracer"):
            with patch("recut.core.tracer._persist_trace", new=AsyncMock()):
                await my_agent("test")

        assert any("trace_if" in r.message for r in caplog.records)

    async def test_trace_if_none_skips_predicate(self):
        """trace_if=None should always trace (no predicate check)."""

        @trace(agent_id="y", sample_rate=1.0, trace_if=None, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            return "result"

        with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
            await my_agent("prompt")

        mock_persist.assert_called_once()


# ===========================================================================
# RECUT_DEFAULT_SAMPLE_RATE env var coercion
# ===========================================================================


class TestSampleRateEnvVar:
    async def test_invalid_sample_rate_env_var_falls_back_to_default(self, caplog):
        """Bad RECUT_DEFAULT_SAMPLE_RATE should log warning and use code default."""
        calls = []

        @trace(agent_id="a", sample_rate=1.0, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            calls.append(prompt)
            return "ok"

        with caplog.at_level(logging.WARNING, logger="recut.core.tracer"):
            with patch.dict(os.environ, {"RECUT_DEFAULT_SAMPLE_RATE": "not-a-float"}):
                with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
                    result = await my_agent("hi")

        assert result == "ok"
        assert calls == ["hi"]
        # sample_rate=1.0 default → should always trace despite bad env var
        mock_persist.assert_called_once()
        assert any("RECUT_DEFAULT_SAMPLE_RATE" in r.message for r in caplog.records)

    async def test_valid_sample_rate_env_var_overrides_default(self):
        """A valid RECUT_DEFAULT_SAMPLE_RATE=0.0 should suppress tracing."""

        @trace(agent_id="a", sample_rate=1.0, provider=_StubProvider())
        async def my_agent(prompt: str, **kwargs) -> str:
            return "ok"

        with patch.dict(os.environ, {"RECUT_DEFAULT_SAMPLE_RATE": "0.0"}):
            with patch("recut.core.tracer._persist_trace", new=AsyncMock()) as mock_persist:
                await my_agent("hi")

        mock_persist.assert_not_called()


# ===========================================================================
# RECUT_EMBEDDING_THRESHOLD env var coercion
# ===========================================================================


class TestEmbeddingThresholdEnvVar:
    async def test_invalid_embedding_threshold_falls_back(self, caplog):
        """Bad RECUT_EMBEDDING_THRESHOLD should not crash; falls back to 0.75."""
        from recut.flagging.engine import _layer2_embeddings

        # Without sentence_transformers installed, this returns [] immediately.
        # But we can verify the env-var path is wrapped by checking no ValueError.
        with caplog.at_level(logging.WARNING, logger="recut.flagging.engine"):
            with patch.dict(os.environ, {"RECUT_EMBEDDING_THRESHOLD": "bad-value"}):
                step = RecutStep(index=0, type=StepType.OUTPUT, content="hello")
                try:
                    result = await _layer2_embeddings(step, [], "original prompt")
                    # Returns [] when numpy/sentence_transformers unavailable — that's fine
                    assert isinstance(result, list)
                except Exception as exc:
                    pytest.fail(f"Unexpected exception with bad env var: {exc}")


# ===========================================================================
# RECUT_CACHE_TTL env var coercion
# ===========================================================================


class TestCacheTTLEnvVar:
    async def test_invalid_cache_ttl_falls_back(self, caplog):
        """Bad RECUT_CACHE_TTL should not crash."""
        from recut.flagging.engine import _cache_flags

        with caplog.at_level(logging.WARNING, logger="recut.flagging.engine"):
            with patch.dict(os.environ, {"RECUT_CACHE_TTL": "not-an-int"}):
                with patch("recut.storage.db.StorageClient") as mock_client:
                    mock_client.return_value.cache_flags = MagicMock()
                    try:
                        await _cache_flags("hash123", [])
                    except Exception as exc:
                        pytest.fail(f"Unexpected exception with bad RECUT_CACHE_TTL: {exc}")

        assert any("RECUT_CACHE_TTL" in r.message for r in caplog.records)

    async def test_zero_cache_ttl_uses_minimum_of_one(self):
        """RECUT_CACHE_TTL=0 should be clamped to 1 second (not 0)."""
        from recut.flagging.engine import _cache_flags, _mem_cache

        _mem_cache.clear()
        with patch.dict(os.environ, {"RECUT_CACHE_TTL": "0"}):
            with patch("recut.storage.db.StorageClient") as mock_client:
                mock_client.return_value.cache_flags = MagicMock()
                await _cache_flags("hash_zero_ttl", [])

        # Entry should be in the L1 cache with an expiry > now
        from datetime import UTC, datetime

        assert "hash_zero_ttl" in _mem_cache
        _, expires_at = _mem_cache["hash_zero_ttl"]
        assert expires_at > datetime.now(UTC)


# ===========================================================================
# score_batch with empty step list
# ===========================================================================


class TestScoreBatchEdgeCases:
    async def test_score_batch_empty_list_returns_empty_dict(self):
        """score_batch([]) should return {} without any errors."""
        from recut.flagging.engine import FlaggingEngine
        from recut.schema.trace import TraceMode

        engine = FlaggingEngine(mode=TraceMode.PEEK)
        result = await engine.score_batch([], "original prompt")
        assert result == {}

    async def test_score_batch_single_step_no_flags(self):
        """A benign step should score without errors."""
        from recut.flagging.engine import FlaggingEngine
        from recut.schema.trace import TraceMode

        engine = FlaggingEngine(mode=TraceMode.PEEK)
        step = RecutStep(index=0, type=StepType.OUTPUT, content="The answer is 42.")
        with patch("recut.flagging.engine._layer4_llm_judge", new=AsyncMock(return_value=[])):
            result = await engine.score_batch([step], "What is 6 times 7?")
        assert isinstance(result, dict)


# ===========================================================================
# FlaggingEngine._layer2_embeddings_batch empty input
# ===========================================================================


class TestEmbeddingsBatchEdgeCases:
    async def test_layer2_batch_empty_list_returns_empty_dict(self):
        """_layer2_embeddings_batch([]) should return {} not crash."""
        from recut.flagging.engine import _layer2_embeddings_batch

        result = await _layer2_embeddings_batch([], "original prompt")
        assert result == {}


# ===========================================================================
# Auditor edge cases
# ===========================================================================


class TestAuditorEdgeCases:
    async def test_build_audit_record_no_flags(self):
        """An audit record with no flags has highest_severity=None."""
        from recut.core.auditor import _build_audit_record
        from recut.schema.audit import AuditMode
        from recut.schema.trace import TraceMeta, TraceMode

        trace_obj = RecutTrace(
            agent_id="test",
            prompt="hello",
            mode=TraceMode.AUDIT,
            meta=TraceMeta(model="m", provider="p"),
        )
        step = RecutStep(index=0, type=StepType.OUTPUT, content="ok", flags=[])
        trace_obj.steps.append(step)

        record = _build_audit_record(trace_obj, AuditMode.AUDIT)
        assert record.flag_count == 0
        assert record.highest_severity is None

    async def test_build_audit_record_all_severity_levels(self):
        """highest_severity returns highest severity level present."""
        from recut.core.auditor import _build_audit_record
        from recut.schema.audit import AuditMode
        from recut.schema.trace import TraceMeta, TraceMode

        trace_obj = RecutTrace(
            agent_id="test",
            prompt="hello",
            mode=TraceMode.AUDIT,
            meta=TraceMeta(model="m", provider="p"),
        )
        step = RecutStep(index=0, type=StepType.OUTPUT, content="ok")
        step.flags = [
            _make_flag(severity=Severity.LOW),
            _make_flag(severity=Severity.MEDIUM),
            _make_flag(severity=Severity.HIGH),
        ]
        trace_obj.steps.append(step)

        record = _build_audit_record(trace_obj, AuditMode.AUDIT)
        assert record.highest_severity == Severity.HIGH
        assert record.flag_count == 3


# ===========================================================================
# Replay bounds checking
# ===========================================================================


class TestReplayCmdBoundsCheck:
    async def test_out_of_bounds_step_index_exits_with_error(self):
        """_replay_async with step_index >= len(trace.steps) should raise Exit."""
        import click

        from recut.cli.commands.replay_cmd import _replay_async
        from recut.schema.trace import TraceMeta, TraceMode

        trace_obj = RecutTrace(
            agent_id="test",
            prompt="hello",
            mode=TraceMode.PEEK,
            meta=TraceMeta(model="m", provider="p"),
        )
        step = RecutStep(index=0, type=StepType.OUTPUT, content="hello")
        trace_obj.steps.append(step)

        mock_client = MagicMock()
        mock_client.load_trace.return_value = trace_obj

        with patch("recut.storage.db.StorageClient", return_value=mock_client):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _replay_async("trace-id-123", 99, '{"injected_content": "x"}')

        assert exc_info.value.exit_code == 1

    async def test_negative_step_index_exits_with_error(self):
        """_replay_async with step_index < 0 should raise Exit."""
        import click

        from recut.cli.commands.replay_cmd import _replay_async
        from recut.schema.trace import TraceMeta, TraceMode

        trace_obj = RecutTrace(
            agent_id="test",
            prompt="hello",
            mode=TraceMode.PEEK,
            meta=TraceMeta(model="m", provider="p"),
        )
        step = RecutStep(index=0, type=StepType.OUTPUT, content="hello")
        trace_obj.steps.append(step)

        mock_client = MagicMock()
        mock_client.load_trace.return_value = trace_obj

        with patch("recut.storage.db.StorageClient", return_value=mock_client):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _replay_async("trace-id-123", -1, '{"injected_content": "x"}')

        assert exc_info.value.exit_code == 1


# ===========================================================================
# CLI commands with missing/invalid trace IDs
# ===========================================================================


class TestCLIInvalidTraceID:
    async def test_peek_cmd_missing_trace_exits(self):
        """_peek_async with an unknown trace ID raises Exit(1)."""
        import click

        from recut.cli.commands.peek_cmd import _peek_async

        mock_client = MagicMock()
        mock_client.load_trace.return_value = None

        with patch("recut.storage.db.StorageClient", return_value=mock_client):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _peek_async("nonexistent-trace-id")

        assert exc_info.value.exit_code == 1

    async def test_audit_cmd_missing_trace_exits(self):
        """_audit_async with an unknown trace ID raises Exit(1)."""
        import click

        from recut.cli.commands.audit_cmd import _audit_async

        mock_client = MagicMock()
        mock_client.load_trace.return_value = None

        with patch("recut.storage.db.StorageClient", return_value=mock_client):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _audit_async("nonexistent-trace-id")

        assert exc_info.value.exit_code == 1

    async def test_replay_cmd_missing_trace_exits(self):
        """_replay_async with an unknown trace ID raises Exit(1)."""
        import click

        from recut.cli.commands.replay_cmd import _replay_async

        mock_client = MagicMock()
        mock_client.load_trace.return_value = None

        with patch("recut.storage.db.StorageClient", return_value=mock_client):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _replay_async("bad-id", 0, '{"injected_content": "x"}')

        assert exc_info.value.exit_code == 1

    async def test_replay_cmd_invalid_json_inject_exits(self):
        """_replay_async with malformed JSON for inject raises Exit(1)."""
        import click

        from recut.cli.commands.replay_cmd import _replay_async
        from recut.schema.trace import TraceMeta, TraceMode

        trace_obj = RecutTrace(
            agent_id="test",
            prompt="hello",
            mode=TraceMode.PEEK,
            meta=TraceMeta(model="m", provider="p"),
        )
        step = RecutStep(index=0, type=StepType.OUTPUT, content="hello")
        trace_obj.steps.append(step)

        mock_client = MagicMock()
        mock_client.load_trace.return_value = trace_obj

        with patch("recut.storage.db.StorageClient", return_value=mock_client):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _replay_async("trace-id", 0, "not-valid-json")

        assert exc_info.value.exit_code == 1


# ===========================================================================
# on_flag decorator edge cases
# ===========================================================================


class TestOnFlagDecorator:
    def setup_method(self):
        """Clear registry before each test."""
        from recut import hooks

        hooks._registry.clear()

    async def test_register_with_severity_filter(self):
        """on_flag(severity="high") only fires for HIGH flags."""
        import recut
        from recut import hooks
        from recut.schema.hooks import RecutFlagEvent

        fired = []

        @recut.on_flag(severity="high")
        def handler(event):
            fired.append(event)

        high_event = RecutFlagEvent(
            trace_id="t1",
            step_id="s1",
            flag=_make_flag(severity=Severity.HIGH),
            suggested_action="review",
            agent_id="agent-1",
        )
        low_event = RecutFlagEvent(
            trace_id="t1",
            step_id="s1",
            flag=_make_flag(severity=Severity.LOW),
            suggested_action="review",
            agent_id="agent-1",
        )

        await hooks.fire_all(high_event)
        await hooks.fire_all(low_event)

        assert len(fired) == 1
        assert fired[0] is high_event

    def test_register_no_args_form(self):
        """on_flag() with no arguments should register handler for all flags."""
        import recut
        from recut import hooks

        @recut.on_flag()
        def handler(event):
            pass

        assert len(hooks._registry) == 1

    def test_register_without_parens_form(self):
        """@recut.on_flag (no call) should register handler for all flags."""
        import recut
        from recut import hooks

        @recut.on_flag
        def handler(event):
            pass

        assert len(hooks._registry) == 1

    def test_multiple_handlers_all_register(self):
        import recut
        from recut import hooks

        @recut.on_flag
        def h1(event):
            pass

        @recut.on_flag
        def h2(event):
            pass

        assert len(hooks._registry) == 2

    async def test_handler_mutation_during_iteration_is_safe(self):
        """Handlers added while firing should not affect the current round."""
        import recut
        from recut import hooks
        from recut.schema.hooks import RecutFlagEvent

        fired_count = []

        @recut.on_flag
        def handler(event):
            fired_count.append(1)
            # Registering another handler during iteration should not double-fire
            hooks._registry.append((lambda e: fired_count.append(99), {}))

        event = RecutFlagEvent(
            trace_id="t",
            step_id="s",
            flag=_make_flag(),
            suggested_action="review",
            agent_id="agent-1",
        )

        await hooks.fire_all(event)

        assert fired_count == [1]  # only the original handler fired, not the dynamically added one


# ===========================================================================
# Stress variant exception isolation
# ===========================================================================


class TestStressVariantExceptions:
    async def test_single_failing_variant_does_not_kill_others(self):
        """A variant that raises should return None and not crash the others."""
        from recut.core.stress import stress
        from recut.schema.fork import (
            ForkDiff,
            ForkInjection,
            ForkType,
            InjectionTarget,
            RecutFork,
        )
        from recut.schema.trace import TraceMeta, TraceMode

        trace_obj = RecutTrace(
            agent_id="test",
            prompt="hello",
            mode=TraceMode.AUDIT,
            meta=TraceMeta(model="m", provider="p"),
        )
        # Two flagged steps — first variant will fail, second should still run
        for i in range(2):
            step = RecutStep(index=i, type=StepType.TOOL_CALL, content=f"tool_{i}()")
            step.flags = [_make_flag(severity=Severity.HIGH, source=FlagSource.RULE)]
            trace_obj.steps.append(step)

        def make_fork(step_index: int) -> RecutFork:
            return RecutFork(
                parent_trace_id=trace_obj.id,
                fork_step_index=step_index,
                fork_type=ForkType.STRESS_VARIANT,
                injection=ForkInjection(
                    target=InjectionTarget.TOOL_RESULT,
                    original_content="x",
                    injected_content="y",
                ),
                replay_steps=[],
                diff=ForkDiff(divergence_step=step_index, plain_summary="ok", risk_delta=0.0),
            )

        replay_calls = []

        async def mock_replay(
            trace, fork_step_index, injection, provider, fork_type=ForkType.MANUAL
        ):
            replay_calls.append(fork_step_index)
            if fork_step_index == 0:
                raise RuntimeError("first variant explodes")
            return make_fork(fork_step_index)

        score_mock = AsyncMock(return_value={})
        with patch("recut.core.stress.replay", side_effect=mock_replay):
            with patch("recut.flagging.engine.FlaggingEngine.score_batch", score_mock):
                runs = await stress(
                    trace=trace_obj,
                    provider=_StubProvider(),
                    num_variants=3,
                )

        # The list should contain only successful variants (None entries filtered out)
        assert isinstance(runs, list)
        # At least one variant should have been attempted for each flagged step
        assert len(replay_calls) >= 1
