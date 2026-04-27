from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from recut.flagging.flags import (
    CONFIDENCE_PHRASES,
    UNCERTAINTY_PHRASES,
    Thresholds,
)
from recut.flagging.prompts import BATCH_FLAGGING_PROMPT, FLAGGING_SYSTEM_PROMPT
from recut.schema.trace import (
    FlagSource,
    FlagType,
    ReasoningSource,
    RecutFlag,
    RecutStep,
    Severity,
    StepType,
    TraceMode,
)

_log = logging.getLogger(__name__)

# Module-level lazy singleton for the meta-LLM client
_meta_client: Any = None


def _get_meta_client() -> Any:
    global _meta_client
    if _meta_client is None:
        import anthropic
        import httpx

        timeout = httpx.Timeout(float(os.environ.get("RECUT_API_TIMEOUT", "30")))
        _meta_client = anthropic.AsyncAnthropic(timeout=timeout)
    return _meta_client


# In-memory L1 flag cache (content_hash → (flags, expires_at))
_mem_cache: dict[str, tuple[list[RecutFlag], datetime]] = {}


class FlaggingEngine:
    """
    Layered flagging engine.

    Layer 1: Rule-based (free, instant)
    Layer 2: Embedding similarity (cheap, optional)
    Layer 3: Native reasoning/action mismatch (free for Claude)
    Layer 4: Batched LLM judge (only when needed and mode allows)
    """

    def __init__(
        self,
        mode: TraceMode = TraceMode.PEEK,
        use_embeddings: bool | None = None,
        use_llm_judge: bool | None = None,
    ):
        self.mode = mode
        self._use_embeddings = (
            use_embeddings
            if use_embeddings is not None
            else (os.environ.get("RECUT_USE_EMBEDDINGS", "true").lower() == "true")
        )
        # LLM judge only available in audit, replay, stress modes
        self._use_llm_judge = (
            use_llm_judge
            if use_llm_judge is not None
            else (mode in (TraceMode.AUDIT, TraceMode.REPLAY, TraceMode.STRESS))
        )

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

        # Layer 1 — rule-based
        flags.extend(_layer1_rules(step, preceding_steps))
        if flags:
            await _cache_flags(cache_key, flags)
            return flags

        # Layer 3 — native mismatch (before embeddings, it's free)
        if step.reasoning and step.reasoning.source == ReasoningSource.NATIVE:
            mismatch_flag = _layer3_native_mismatch(step)
            if mismatch_flag:
                flags.append(mismatch_flag)
                await _cache_flags(cache_key, flags)
                return flags

        # Layer 2 — embedding similarity
        if self._use_embeddings:
            embedding_flags = await _layer2_embeddings(step, preceding_steps, original_prompt)
            flags.extend(embedding_flags)

        # Layer 4 — batched LLM judge (only for high-stakes steps in qualifying modes)
        if not flags and self._use_llm_judge and step.type in (StepType.TOOL_CALL, StepType.OUTPUT):
            llm_flags = await _layer4_llm_judge([step], original_prompt)
            flags.extend(llm_flags)

        await _cache_flags(cache_key, flags)
        return flags

    async def score_batch(
        self,
        steps: list[RecutStep],
        original_prompt: str,
    ) -> dict[str, list[RecutFlag]]:
        """Score multiple steps at once. Layer 4 batches these into one LLM call."""
        results: dict[str, list[RecutFlag]] = {}

        llm_candidates: list[RecutStep] = []

        for i, step in enumerate(steps):
            preceding = steps[max(0, i - 2) : i]
            cache_key = _cache_key(step, preceding)
            cached = await _get_cached_flags(cache_key)

            if cached is not None:
                results[step.id] = cached
                continue

            step_flags: list[RecutFlag] = []

            # Layers 1 and 3
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
            else:
                llm_candidates.append(step)

        # Batch the LLM judge call for all candidates at once
        if llm_candidates and self._use_llm_judge:
            llm_results = await _layer4_llm_judge(llm_candidates, original_prompt)
            # llm_results is a flat list — assign back by step_id in flag.step_id
            for flag in llm_results:
                results.setdefault(flag.step_id, []).append(flag)

        return results


# ---------------------------------------------------------------------------
# Layer 1 — Rule-based
# ---------------------------------------------------------------------------


def _layer1_rules(step: RecutStep, preceding: list[RecutStep]) -> list[RecutFlag]:
    flags: list[RecutFlag] = []

    # Reasoning block empty but non-reasoning action taken
    if (
        step.type in (StepType.TOOL_CALL, StepType.OUTPUT)
        and step.reasoning
        and not step.reasoning.content.strip()
    ):
        flags.append(
            RecutFlag(
                type=FlagType.REASONING_GAP,
                severity=Severity.MEDIUM,
                plain_reason="The agent took an action without any reasoning — it's unclear why it made this choice.",
                step_id=step.id,
                source=FlagSource.RULE,
            )
        )

    # Tool call with no preceding reasoning step at all
    if (
        step.type == StepType.TOOL_CALL
        and step.reasoning is None
        and not any(p.type == StepType.REASONING for p in preceding)
    ):
        flags.append(
            RecutFlag(
                type=FlagType.ANOMALOUS_TOOL_USE,
                severity=Severity.LOW,
                plain_reason="The agent used a tool without any visible reasoning beforehand — worth a quick look.",
                step_id=step.id,
                source=FlagSource.RULE,
            )
        )

    # Repeated identical tool calls
    if step.type == StepType.TOOL_CALL and preceding:
        identical = [
            p for p in preceding if p.type == StepType.TOOL_CALL and p.content == step.content
        ]
        if identical:
            flags.append(
                RecutFlag(
                    type=FlagType.ANOMALOUS_TOOL_USE,
                    severity=Severity.HIGH,
                    plain_reason="The agent called the same tool with identical inputs more than once — this looks like a loop.",
                    step_id=step.id,
                    source=FlagSource.RULE,
                )
            )

    # Step count exceeds reasonable range (scope creep heuristic)
    if step.index > 20:
        flags.append(
            RecutFlag(
                type=FlagType.SCOPE_CREEP,
                severity=Severity.LOW,
                plain_reason=f"The agent is on step {step.index + 1}, which is more steps than expected for most tasks.",
                step_id=step.id,
                source=FlagSource.RULE,
            )
        )

    return flags


# ---------------------------------------------------------------------------
# Layer 3 — Native reasoning/action mismatch (Claude only)
# ---------------------------------------------------------------------------


def _layer3_native_mismatch(step: RecutStep) -> RecutFlag | None:
    if step.reasoning is None or step.reasoning.source != ReasoningSource.NATIVE:
        return None

    reasoning_lower = step.reasoning.content.lower()
    content_lower = step.content.lower()

    thinking_uncertain = any(p in reasoning_lower for p in UNCERTAINTY_PHRASES)
    acting_confident = any(p in content_lower for p in CONFIDENCE_PHRASES)

    if thinking_uncertain and acting_confident:
        return RecutFlag(
            type=FlagType.REASONING_ACTION_MISMATCH,
            severity=Severity.HIGH,
            plain_reason=(
                "The agent seemed unsure in its thinking but acted confidently anyway — "
                "worth a closer look. Its stated uncertainty didn't match how it behaved."
            ),
            step_id=step.id,
            source=FlagSource.NATIVE,
        )

    return None


# ---------------------------------------------------------------------------
# Layer 2 — Embedding similarity (optional)
# ---------------------------------------------------------------------------


async def _layer2_embeddings(
    step: RecutStep,
    preceding: list[RecutStep],
    original_prompt: str,
) -> list[RecutFlag]:
    """
    Uses cosine similarity to detect goal drift and reasoning/action mismatch.
    Falls back gracefully if sentence-transformers is not installed.
    """
    try:
        import numpy as np
    except ImportError:
        return []

    threshold = float(os.environ.get("RECUT_EMBEDDING_THRESHOLD", "0.75"))

    try:
        model = _get_embedding_model()
        flags: list[RecutFlag] = []

        prompt_emb = model.encode(original_prompt)
        step_emb = model.encode(step.content)
        similarity = float(
            np.dot(prompt_emb, step_emb)
            / (np.linalg.norm(prompt_emb) * np.linalg.norm(step_emb) + 1e-10)
        )

        if similarity < (1.0 - threshold):
            flags.append(
                RecutFlag(
                    type=FlagType.GOAL_DRIFT,
                    severity=Severity.MEDIUM,
                    plain_reason=(
                        "The agent's response seems to have drifted away from the original task. "
                        f"Similarity to the original goal: {similarity:.0%}."
                    ),
                    step_id=step.id,
                    source=FlagSource.EMBEDDING,
                )
            )

        if step.reasoning and step.reasoning.content:
            reasoning_emb = model.encode(step.reasoning.content)
            ra_similarity = float(
                np.dot(reasoning_emb, step_emb)
                / (np.linalg.norm(reasoning_emb) * np.linalg.norm(step_emb) + 1e-10)
            )
            if ra_similarity < (1.0 - threshold):
                flags.append(
                    RecutFlag(
                        type=FlagType.REASONING_ACTION_MISMATCH,
                        severity=Severity.MEDIUM,
                        plain_reason=(
                            "The agent's reasoning and its actual action don't seem closely related. "
                            "It may have reasoned about one thing and done another."
                        ),
                        step_id=step.id,
                        source=FlagSource.EMBEDDING,
                    )
                )

        return flags
    except Exception:
        return []


_embedding_model: Any = None


def _get_embedding_model() -> Any:
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model


# ---------------------------------------------------------------------------
# Layer 4 — Batched LLM judge
# ---------------------------------------------------------------------------


def _parse_llm_flags(raw: str, steps: list[RecutStep]) -> list[RecutFlag]:
    """Parse the LLM judge response into RecutFlag objects."""
    thresholds = Thresholds()
    flags: list[RecutFlag] = []

    results = json.loads(raw)  # raises json.JSONDecodeError on bad input

    if not isinstance(results, list):
        _log.warning("recut: Layer 4 response is not a JSON array")
        return []

    step_map = {s.id: s for s in steps}

    for result in results:
        step_id = result.get("step_id", "")
        plain_reasons = result.get("plain_reasons", {})

        for flag_name, score in result.items():
            if flag_name in ("step_id", "plain_reasons"):
                continue
            if not isinstance(score, (int, float)):
                continue
            if score < thresholds.LOW:
                continue

            try:
                flag_type = FlagType(flag_name)
            except ValueError:
                continue

            # Use step from map if available, else keep raw step_id
            resolved_id = step_id if step_id in step_map else step_id

            severity = (
                Severity.HIGH
                if score >= thresholds.HIGH
                else Severity.MEDIUM
                if score >= thresholds.MEDIUM
                else Severity.LOW
            )

            flags.append(
                RecutFlag(
                    type=flag_type,
                    severity=severity,
                    plain_reason=plain_reasons.get(
                        flag_name, f"Flagged by meta-LLM with score {score:.2f}."
                    ),
                    step_id=resolved_id,
                    source=FlagSource.LLM,
                )
            )

    return flags


async def _layer4_llm_judge(
    steps: list[RecutStep],
    original_prompt: str,
) -> list[RecutFlag]:
    """Call a cheap meta-LLM to judge multiple steps in one batched request."""
    import asyncio

    import anthropic

    meta_model = os.environ.get("RECUT_META_MODEL", "claude-haiku-4-5-20251001")

    steps_payload = json.dumps(
        [
            {
                "step_id": s.id,
                "index": s.index,
                "type": s.type.value,
                "content": s.content[:500],
                "reasoning": s.reasoning.content[:300] if s.reasoning else None,
            }
            for s in steps
        ],
        indent=2,
    )

    prompt = BATCH_FLAGGING_PROMPT.format(
        prompt=original_prompt[:300],
        steps_json=steps_payload,
    )

    raw = ""
    for attempt in range(3):
        try:
            response = await _get_meta_client().messages.create(
                model=meta_model,
                max_tokens=2000,
                system=FLAGGING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            if not response.content:
                return []
            block = response.content[0]
            raw = block.text.strip() if hasattr(block, "text") else ""
            return _parse_llm_flags(raw, steps)
        except anthropic.RateLimitError:
            if attempt < 2:
                await asyncio.sleep(5 * (attempt + 1))
            else:
                _log.warning("recut: Layer 4 rate-limited after 3 attempts, skipping")
                return []
        except anthropic.APIConnectionError:
            if attempt < 2:
                await asyncio.sleep(2**attempt)
            else:
                _log.warning("recut: Layer 4 connection error after 3 attempts, skipping")
                return []
        except json.JSONDecodeError as exc:
            _log.warning("recut: Layer 4 returned non-JSON: %.80s…", raw, exc_info=exc)
            return []
        except Exception as exc:
            _log.warning("recut: Layer 4 unexpected error: %s", exc)
            return []

    return []


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------


def _cache_key(step: RecutStep, preceding: list[RecutStep]) -> str:
    context = step.content + "".join(p.content for p in preceding[-2:])
    return hashlib.sha256(context.encode()).hexdigest()


async def _get_cached_flags(content_hash: str) -> list[RecutFlag] | None:
    if os.environ.get("RECUT_CACHE_ENABLED", "true").lower() != "true":
        return None

    # L1: in-memory cache
    entry = _mem_cache.get(content_hash)
    if entry is not None and datetime.now(UTC) < entry[1]:
        return entry[0]

    try:
        import asyncio

        from recut.storage.db import StorageClient

        client = StorageClient()
        loop = asyncio.get_running_loop()
        row = await loop.run_in_executor(None, client.get_cached_flags, content_hash)
        if row is None:
            return None
        data = json.loads(row.flags_json)
        return [RecutFlag(**f) for f in data]
    except Exception:
        return None


async def _cache_flags(content_hash: str, flags: list[RecutFlag]) -> None:
    if os.environ.get("RECUT_CACHE_ENABLED", "true").lower() != "true":
        return

    # Populate in-memory cache
    ttl = int(os.environ.get("RECUT_CACHE_TTL", "3600"))
    _mem_cache[content_hash] = (flags, datetime.now(UTC) + timedelta(seconds=ttl))

    try:
        import asyncio

        from recut.storage.db import StorageClient
        from recut.storage.models import FlagCache

        now = datetime.now(UTC)
        row = FlagCache(
            content_hash=content_hash,
            flags_json=json.dumps([f.model_dump(mode="json") for f in flags]),
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        client = StorageClient()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.save_flag_cache, row)
    except Exception:
        pass
