from __future__ import annotations

import asyncio
import json

from recut.flagging.engine import FlaggingEngine
from recut.plain.summariser import summarise_step
from recut.providers.base import AbstractProvider
from recut.schema.fork import ForkDiff, ForkInjection, ForkType, RecutFork
from recut.schema.trace import RecutStep, RecutTrace
from recut.storage.circuit_breaker import is_open, record_failure, record_success
from recut.storage.db import StorageClient
from recut.storage.models import ForkRow


async def replay(
    trace: RecutTrace,
    fork_step_index: int,
    injection: ForkInjection,
    provider: AbstractProvider,
    fork_type: ForkType = ForkType.MANUAL,
) -> RecutFork:
    """
    Fork a trace at a specific step, inject modified content, and run forward.

    The original steps up to fork_step_index are preserved. New steps from
    the fork point onward are re-run with the injection applied.
    """
    replayed_steps = await provider.replay_from(
        steps=trace.steps,
        fork_index=fork_step_index,
        injection=injection.model_dump(),
    )

    engine = FlaggingEngine(mode=trace.mode)
    for i, step in enumerate(replayed_steps):
        preceding = replayed_steps[max(0, i - 2) : i]
        flags = await engine.score_step(step, preceding, trace.prompt)
        step.flags = flags
        step.plain_summary = summarise_step(step, trace.language)

    fork = RecutFork(
        parent_trace_id=trace.id,
        fork_step_index=fork_step_index,
        fork_type=fork_type,
        injection=injection,
        replay_steps=[s.model_dump(mode="json") for s in replayed_steps],
    )

    fork.diff = _compute_diff(
        original_steps=trace.steps[fork_step_index:],
        replayed_steps=replayed_steps,
        fork_index=fork_step_index,
    )

    await _persist_fork(fork)
    return fork


async def diff(trace: RecutTrace, fork: RecutFork) -> ForkDiff:
    """Compute or return the diff between a trace and a fork."""
    if fork.diff is not None:
        return fork.diff

    original = trace.steps[fork.fork_step_index:]
    replayed = [RecutStep(**s) for s in fork.replay_steps]
    return _compute_diff(original, replayed, fork.fork_step_index)


def _compute_diff(
    original_steps: list[RecutStep],
    replayed_steps: list[RecutStep],
    fork_index: int,
) -> ForkDiff:
    divergence_step = fork_index

    for i, (orig, rep) in enumerate(zip(original_steps, replayed_steps, strict=False)):
        if orig.content != rep.content:
            divergence_step = fork_index + i
            break

    orig_risk = max((s.risk_score for s in original_steps), default=0.0)
    replay_risk = max((s.risk_score for s in replayed_steps), default=0.0)
    risk_delta = replay_risk - orig_risk

    if abs(risk_delta) < 0.1:
        summary = "The agent behaved similarly after the change — the injection had little visible effect."
    elif risk_delta > 0:
        summary = (
            f"The agent became riskier after the change (risk increased by {risk_delta:.0%}). "
            "The injected content may have destabilized its behavior."
        )
    else:
        summary = (
            f"The agent became more cautious after the change (risk decreased by {abs(risk_delta):.0%}). "
            "The injected content appeared to improve its behavior."
        )

    return ForkDiff(
        divergence_step=divergence_step,
        plain_summary=summary,
        risk_delta=round(risk_delta, 3),
    )


async def _persist_fork(fork: RecutFork) -> None:
    if is_open():
        return
    try:
        row = ForkRow(
            id=fork.id,
            created_at=fork.created_at,
            parent_trace_id=fork.parent_trace_id,
            fork_step_index=fork.fork_step_index,
            fork_type=fork.fork_type.value,
            injection_json=fork.injection.model_dump_json(),
            replay_steps_json=json.dumps(fork.replay_steps),
            diff_json=fork.diff.model_dump_json() if fork.diff else None,
        )
        loop = asyncio.get_running_loop()
        client = StorageClient()
        await loop.run_in_executor(None, client.save_fork_row, row)
        record_success()
    except Exception:  # noqa: BLE001
        record_failure()
