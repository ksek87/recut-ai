"""
Layered flagging engine — orchestrates all four detection layers.

Layer 1 (rules.py):      deterministic rule checks, free & instant
Layer 2 (embeddings.py): cosine similarity goal-drift + RA mismatch
Layer 3 (native.py):     Claude native thinking uncertainty vs action
Layer 4 (llm_judge.py):  batched meta-LLM judge, only when flagging_depth="full"

Set flagging_depth="fast" (default) to run layers 1-3 only.
Set flagging_depth="full" for compliance passes that include the LLM judge.
"""

from __future__ import annotations

import os
from typing import Literal

from recut.flagging.cache import (
    _cache_flags,
    _cache_key,
    _get_cached_flags,
    _mem_cache,
)
from recut.flagging.layers.embeddings import (
    layer2_embeddings as _layer2_embeddings,
)
from recut.flagging.layers.embeddings import (
    layer2_embeddings_batch as _layer2_embeddings_batch,
)
from recut.flagging.layers.llm_judge import (
    _call_l4_api,
    _get_l4_client,
    _l4_clients,
    _layer4_llm_judge,
    _parse_llm_flags,
)
from recut.flagging.layers.native import layer3_native_mismatch as _layer3_native_mismatch
from recut.flagging.layers.rules import layer1_rules as _layer1_rules
from recut.schema.trace import ReasoningSource, RecutFlag, RecutStep, StepType, TraceMode

# Re-export private names so existing patch targets and direct test imports keep working.
__all__ = [
    "FlaggingEngine",
    "_cache_flags",
    "_cache_key",
    "_call_l4_api",
    "_get_cached_flags",
    "_get_l4_client",
    "_l4_clients",
    "_layer1_rules",
    "_layer2_embeddings",
    "_layer2_embeddings_batch",
    "_layer3_native_mismatch",
    "_layer4_llm_judge",
    "_mem_cache",
    "_parse_llm_flags",
]


class FlaggingEngine:
    """Orchestrates all four flagging layers for a single trace."""

    def __init__(
        self,
        mode: TraceMode = TraceMode.PEEK,
        use_embeddings: bool | None = None,
        use_llm_judge: bool | None = None,
        flagging_depth: Literal["fast", "full"] = "fast",
    ):
        self.mode = mode
        self.flagging_depth = flagging_depth
        self._use_embeddings = (
            use_embeddings
            if use_embeddings is not None
            else (os.environ.get("RECUT_USE_EMBEDDINGS", "true").lower() == "true")
        )
        if use_llm_judge is not None:
            self._use_llm_judge = use_llm_judge
        else:
            self._use_llm_judge = flagging_depth == "full"

    async def score_step(
        self,
        step: RecutStep,
        preceding_steps: list[RecutStep],
        original_prompt: str,
    ) -> list[RecutFlag]:
        """Score a single step through all applicable layers."""
        cache_key = _cache_key(step, preceding_steps)
        cached = await _get_cached_flags(cache_key)
        if cached is not None:
            return cached

        flags: list[RecutFlag] = []

        flags.extend(_layer1_rules(step, preceding_steps))
        if flags:
            await _cache_flags(cache_key, flags)
            return flags

        if step.reasoning and step.reasoning.source == ReasoningSource.NATIVE:
            mismatch_flag = _layer3_native_mismatch(step)
            if mismatch_flag:
                flags.append(mismatch_flag)
                await _cache_flags(cache_key, flags)
                return flags

        if self._use_embeddings:
            flags.extend(await _layer2_embeddings(step, preceding_steps, original_prompt))

        if not flags and self._use_llm_judge and step.type in (StepType.TOOL_CALL, StepType.OUTPUT):
            flags.extend(await _layer4_llm_judge([step], original_prompt))

        await _cache_flags(cache_key, flags)
        return flags

    async def score_batch(
        self,
        steps: list[RecutStep],
        original_prompt: str,
    ) -> dict[str, list[RecutFlag]]:
        """Score multiple steps. Layers 1+3 per-step; Layer 2 batch-encoded; Layer 4 one call."""
        results: dict[str, list[RecutFlag]] = {}
        embedding_candidates: list[RecutStep] = []
        llm_candidates: list[RecutStep] = []

        for i, step in enumerate(steps):
            preceding = steps[max(0, i - 2) : i]
            cache_key = _cache_key(step, preceding)
            cached = await _get_cached_flags(cache_key)

            if cached is not None:
                results[step.id] = cached
                continue

            step_flags: list[RecutFlag] = []
            step_flags.extend(_layer1_rules(step, preceding))
            if (
                not step_flags
                and step.reasoning
                and step.reasoning.source == ReasoningSource.NATIVE
            ):
                mf = _layer3_native_mismatch(step)
                if mf:
                    step_flags.append(mf)

            if step_flags:
                results[step.id] = step_flags
                await _cache_flags(cache_key, step_flags)
            elif self._use_embeddings:
                embedding_candidates.append(step)
            else:
                llm_candidates.append(step)

        if embedding_candidates:
            emb_results = await _layer2_embeddings_batch(embedding_candidates, original_prompt)
            for step in embedding_candidates:
                flags = emb_results.get(step.id, [])
                if flags:
                    results[step.id] = flags
                    preceding = steps[max(0, step.index - 2) : step.index]
                    await _cache_flags(_cache_key(step, preceding), flags)
                else:
                    llm_candidates.append(step)

        if llm_candidates and self._use_llm_judge:
            for flag in await _layer4_llm_judge(llm_candidates, original_prompt):
                results.setdefault(flag.step_id, []).append(flag)

        return results
