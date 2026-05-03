"""
Behavioral fingerprinting: detect statistical deviations from per-agent baselines.

After a minimum of _MIN_HISTORY traces are available for an agent, each new run
is compared against the per-agent baseline (mean/stddev of step count and average
risk score). Deviations beyond _ZSCORE_THRESHOLD standard deviations are flagged
with FlagSource.FINGERPRINT — no model call, no API, pure math on local data.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence

from recut.schema.trace import FlagSource, FlagType, RecutFlag, RecutTrace, Severity

_log = logging.getLogger(__name__)

_MIN_HISTORY = 5
_ZSCORE_THRESHOLD = 2.5
_ZSCORE_HIGH_THRESHOLD = 3.5


def get_fingerprint_flags(trace: RecutTrace, history: list[RecutTrace]) -> list[RecutFlag]:
    """Compare trace metrics against per-agent baseline from history.

    Returns an empty list when history is too short or no anomalies are found.
    Flags attach a step_id pointing to the trace's final step.
    """
    if len(history) < _MIN_HISTORY or not trace.steps:
        return []

    anchor_step_id = trace.steps[-1].id
    flags: list[RecutFlag] = []

    step_counts = [len(t.steps) for t in history]
    risk_avgs = [_avg_risk(t) for t in history]

    z_steps = _zscore(len(trace.steps), step_counts)
    if z_steps is not None and z_steps > _ZSCORE_THRESHOLD:
        baseline_avg = _mean(step_counts)
        flags.append(
            RecutFlag(
                type=FlagType.SCOPE_CREEP,
                severity=Severity.HIGH if z_steps >= _ZSCORE_HIGH_THRESHOLD else Severity.MEDIUM,
                plain_reason=(
                    f"This run used {len(trace.steps)} steps — {z_steps:.1f}σ above this "
                    f"agent's baseline ({baseline_avg:.0f} steps avg). Possible scope creep."
                ),
                step_id=anchor_step_id,
                source=FlagSource.FINGERPRINT,
            )
        )

    current_risk = _avg_risk(trace)
    z_risk = _zscore(current_risk, risk_avgs)
    if z_risk is not None and z_risk > _ZSCORE_THRESHOLD:
        baseline_avg = _mean(risk_avgs)
        flags.append(
            RecutFlag(
                type=FlagType.OVERCONFIDENCE,
                severity=Severity.HIGH if z_risk >= _ZSCORE_HIGH_THRESHOLD else Severity.MEDIUM,
                plain_reason=(
                    f"Average risk score this run ({current_risk:.2f}) is {z_risk:.1f}σ above "
                    f"this agent's baseline ({baseline_avg:.2f} avg). Unusually risky behavior."
                ),
                step_id=anchor_step_id,
                source=FlagSource.FINGERPRINT,
            )
        )

    return flags


def _avg_risk(trace: RecutTrace) -> float:
    scored = [s.risk_score for s in trace.steps if s.risk_score > 0]
    return _mean(scored)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _zscore(value: float, population: Sequence[float]) -> float | None:
    if len(population) < 2:
        return None
    sd = _stddev(population)
    if sd == 0.0:
        return None
    return (value - _mean(population)) / sd
