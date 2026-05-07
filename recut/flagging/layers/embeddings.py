"""Layer 2: embedding-based goal-drift and reasoning/action mismatch detection."""

from __future__ import annotations

import logging
import os
from typing import Any

from recut.schema.trace import FlagSource, FlagType, RecutFlag, RecutStep, Severity
from recut.utils import parse_float_env, parse_int_env

_log = logging.getLogger(__name__)

_embedding_model: Any = None


def _get_embedding_model_name() -> str:
    return os.environ.get("RECUT_EMBEDDING_MODEL", "all-MiniLM-L6-v2")


try:
    import numpy as np

    _NUMPY_AVAILABLE = True
except ImportError:
    np = None
    _NUMPY_AVAILABLE = False

try:
    from sentence_transformers import (
        SentenceTransformer as _SentenceTransformer,
    )

    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False

_RA_MISMATCH_REASON = (
    "The agent's reasoning and its actual action don't seem closely related. "
    "It may have reasoned about one thing and done another."
)


def get_embedding_threshold() -> float:
    return parse_float_env("RECUT_EMBEDDING_THRESHOLD", 0.75)


def cosine_sim(a: Any, b: Any) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def _goal_drift_flag(step_id: str, similarity: float) -> RecutFlag:
    return RecutFlag(
        type=FlagType.GOAL_DRIFT,
        severity=Severity.MEDIUM,
        plain_reason=(
            "The agent's response seems to have drifted away from the original task. "
            f"Similarity to the original goal: {similarity:.0%}."
        ),
        step_id=step_id,
        source=FlagSource.EMBEDDING,
    )


def _ra_mismatch_flag(step_id: str) -> RecutFlag:
    return RecutFlag(
        type=FlagType.REASONING_ACTION_MISMATCH,
        severity=Severity.MEDIUM,
        plain_reason=_RA_MISMATCH_REASON,
        step_id=step_id,
        source=FlagSource.EMBEDDING,
    )


def _get_embedding_model() -> Any:
    global _embedding_model
    if not _ST_AVAILABLE:
        raise ImportError("sentence-transformers not installed")
    if _embedding_model is None:
        _embedding_model = _SentenceTransformer(_get_embedding_model_name())
    return _embedding_model


async def layer2_embeddings(
    step: RecutStep,
    preceding: list[RecutStep],
    original_prompt: str,
) -> list[RecutFlag]:
    """Single-step cosine similarity check. Returns [] if deps unavailable."""
    if not _NUMPY_AVAILABLE:
        return []

    threshold = get_embedding_threshold()
    try:
        model = _get_embedding_model()
        flags: list[RecutFlag] = []
        prompt_emb = model.encode(original_prompt)
        step_emb = model.encode(step.content)
        sim = cosine_sim(prompt_emb, step_emb)
        if sim < (1.0 - threshold):
            flags.append(_goal_drift_flag(step.id, sim))
        if step.reasoning and step.reasoning.content:
            r_emb = model.encode(step.reasoning.content)
            if cosine_sim(r_emb, step_emb) < (1.0 - threshold):
                flags.append(_ra_mismatch_flag(step.id))
        return flags
    except Exception:
        return []


async def layer2_embeddings_batch(
    steps: list[RecutStep],
    original_prompt: str,
) -> dict[str, list[RecutFlag]]:
    """Batch-encode all steps in one model.encode() call."""
    if not _NUMPY_AVAILABLE or not steps:
        return {}

    threshold = get_embedding_threshold()
    try:
        model = _get_embedding_model()
        reasoning_contents = [
            s.reasoning.content if s.reasoning and s.reasoning.content else "" for s in steps
        ]
        all_texts = [original_prompt] + [s.content for s in steps] + reasoning_contents
        batch_size = parse_int_env("RECUT_EMBEDDING_BATCH_SIZE", 32, minimum=1)
        all_embs = model.encode(all_texts, batch_size=batch_size, show_progress_bar=False)

        prompt_emb = all_embs[0]
        step_embs = all_embs[1 : 1 + len(steps)]
        reasoning_embs = all_embs[1 + len(steps) :]

        results: dict[str, list[RecutFlag]] = {}
        for i, step in enumerate(steps):
            flags: list[RecutFlag] = []
            step_emb = step_embs[i]
            sim = cosine_sim(prompt_emb, step_emb)
            if sim < (1.0 - threshold):
                flags.append(_goal_drift_flag(step.id, sim))
            if reasoning_contents[i] and cosine_sim(reasoning_embs[i], step_emb) < (
                1.0 - threshold
            ):
                flags.append(_ra_mismatch_flag(step.id))
            if flags:
                results[step.id] = flags
        return results
    except Exception:
        return {}
