from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import anthropic
import openai

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


class FlaggingEngine:
    """
    Layered flagging engine.

    Layer 1: Rule-based (free, instant)
    Layer 2: Embedding similarity (cheap, optional)
    Layer 3: Native reasoning/action mismatch (free for Claude)
    Layer 4: Batched LLM judge (only when flagging_depth="full")

    Set flagging_depth="fast" (default) to run layers 1-3 only — zero meta-LLM cost.
    Set flagging_depth="full" for compliance audit passes that include the LLM judge.
    """

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
        # Layer 4 enabled only when flagging_depth="full"; use_llm_judge can override
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
        """Score multiple steps at once. Layers 1+3 per-step; Layer 2 batch-encoded; Layer 4 one LLM call."""
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
                if self._use_embeddings:
                    embedding_candidates.append(step)
                else:
                    llm_candidates.append(step)

        # Layer 2 — batch-encode all embedding candidates at once
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

        # Layer 4 — batch the LLM judge call for all remaining candidates
        if llm_candidates and self._use_llm_judge:
            llm_results = await _layer4_llm_judge(llm_candidates, original_prompt)
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
# Layer 2 — Batch variant (used by score_batch)
# ---------------------------------------------------------------------------


async def _layer2_embeddings_batch(
    steps: list[RecutStep],
    original_prompt: str,
) -> dict[str, list[RecutFlag]]:
    """Batch-encode all steps in one model.encode() call for score_batch paths."""
    try:
        import numpy as np
    except ImportError:
        return {}

    if not steps:
        return {}

    threshold = float(os.environ.get("RECUT_EMBEDDING_THRESHOLD", "0.75"))

    try:
        model = _get_embedding_model()
        contents = [s.content for s in steps]
        reasoning_contents = [
            s.reasoning.content if s.reasoning and s.reasoning.content else "" for s in steps
        ]

        all_texts = [original_prompt] + contents + reasoning_contents
        all_embs = model.encode(all_texts, batch_size=32, show_progress_bar=False)

        prompt_emb = all_embs[0]
        step_embs = all_embs[1 : 1 + len(steps)]
        reasoning_embs = all_embs[1 + len(steps) :]

        results: dict[str, list[RecutFlag]] = {}
        for i, step in enumerate(steps):
            flags: list[RecutFlag] = []
            step_emb = step_embs[i]
            norm_p = np.linalg.norm(prompt_emb) * np.linalg.norm(step_emb) + 1e-10
            similarity = float(np.dot(prompt_emb, step_emb) / norm_p)
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
            r_content = reasoning_contents[i]
            if r_content:
                r_emb = reasoning_embs[i]
                norm_ra = np.linalg.norm(r_emb) * np.linalg.norm(step_emb) + 1e-10
                ra_sim = float(np.dot(r_emb, step_emb) / norm_ra)
                if ra_sim < (1.0 - threshold):
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
            if flags:
                results[step.id] = flags
        return results
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Layer 4 — BYOM backend dispatcher
# Set RECUT_L4_BACKEND=local (default) | anthropic | openai
# ---------------------------------------------------------------------------

_L4_VALID_BACKENDS = frozenset({"local", "anthropic", "openai"})

# One singleton client per backend (keyed by backend name)
_l4_clients: dict[str, Any] = {}

_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "local": "llama3",
}


def _get_l4_client(backend: str) -> Any:
    if backend not in _l4_clients:
        import httpx

        timeout = httpx.Timeout(float(os.environ.get("RECUT_API_TIMEOUT", "30")))
        if backend == "anthropic":
            _l4_clients[backend] = anthropic.AsyncAnthropic(timeout=timeout)
        else:
            base_url = (
                os.environ.get("RECUT_L4_LOCAL_URL", "http://localhost:11434/v1")
                if backend == "local"
                else None
            )
            api_key = "local" if backend == "local" else os.environ.get("OPENAI_API_KEY", "no-key")
            kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
            if base_url:
                kwargs["base_url"] = base_url
            _l4_clients[backend] = openai.AsyncOpenAI(**kwargs)
    return _l4_clients[backend]


async def _call_l4_api(backend: str, system: str, user_prompt: str, meta_model: str) -> str:
    """Dispatch to the configured L4 backend. Returns raw text or raises."""
    client = _get_l4_client(backend)
    if backend == "anthropic":
        response = await client.messages.create(
            model=meta_model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if not response.content:
            return ""
        block = response.content[0]
        return block.text.strip() if hasattr(block, "text") else ""
    else:
        response = await client.chat.completions.create(
            model=meta_model,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
        )
        if not response.choices:
            return ""
        return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Layer 4 — Batched LLM judge
# ---------------------------------------------------------------------------


async def _layer4_llm_judge(
    steps: list[RecutStep],
    original_prompt: str,
) -> list[RecutFlag]:
    """Call the configured meta-LLM to judge multiple steps in one batched request."""
    backend = os.environ.get("RECUT_L4_BACKEND", "local").lower()
    if backend not in _L4_VALID_BACKENDS:
        _log.warning("recut: Unknown RECUT_L4_BACKEND=%r, defaulting to local", backend)
        backend = "local"

    meta_model = os.environ.get("RECUT_META_MODEL", _DEFAULT_MODELS[backend])

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
            raw = await _call_l4_api(backend, FLAGGING_SYSTEM_PROMPT, prompt, meta_model)
            return _parse_llm_flags(raw, steps)
        except (anthropic.AuthenticationError, openai.AuthenticationError) as exc:
            _log.warning("recut: Layer 4 auth error (backend=%r): %s", backend, exc)
            return []
        except (anthropic.RateLimitError, openai.RateLimitError):
            if attempt < 2:
                await asyncio.sleep(5 * (attempt + 1))
            else:
                _log.warning("recut: Layer 4 rate-limited after 3 attempts, skipping")
                return []
        except (anthropic.APIConnectionError, openai.APIConnectionError):
            if backend == "local":
                _log.debug("recut: L4 local backend unreachable, skipping")
                return []
            if attempt < 2:
                await asyncio.sleep(2**attempt)
            else:
                _log.warning("recut: Layer 4 connection error after 3 attempts, skipping")
                return []
        except json.JSONDecodeError as exc:
            _log.warning("recut: Layer 4 returned non-JSON (%.80s…): %s", raw, exc)
            return []
        except Exception as exc:
            _log.warning("recut: Layer 4 unexpected error: %s", exc)
            return []
    return []


def _parse_llm_flags(raw: str, steps: list[RecutStep]) -> list[RecutFlag]:
    """Parse the JSON response from the LLM judge into RecutFlag objects."""
    try:
        results = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("recut: Layer 4 returned non-JSON (%.80s…): %s", raw, exc)
        return []

    flags: list[RecutFlag] = []
    thresholds = Thresholds()

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
                    step_id=step_id,
                    source=FlagSource.LLM,
                )
            )

    return flags


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------

# L1 in-memory cache: content_hash -> (flags, expires_at)
_mem_cache: dict[str, tuple[list[RecutFlag], datetime]] = {}


def _cache_key(step: RecutStep, preceding: list[RecutStep]) -> str:
    context = step.content + "".join(p.content for p in preceding[-2:])
    return hashlib.sha256(context.encode()).hexdigest()


async def _get_cached_flags(content_hash: str) -> list[RecutFlag] | None:
    if os.environ.get("RECUT_CACHE_ENABLED", "true").lower() != "true":
        return None

    # L1: check in-memory first (no I/O)
    entry = _mem_cache.get(content_hash)
    if entry is not None:
        flags, expires_at = entry
        if datetime.now(UTC) < expires_at:
            return flags
        del _mem_cache[content_hash]

    try:
        from recut.storage.db import StorageClient

        client = StorageClient()
        loop = asyncio.get_running_loop()
        row = await loop.run_in_executor(None, client.get_cached_flags, content_hash)
        if row is None:
            return None
        data = json.loads(row.flags_json)
        return [RecutFlag(**f) for f in data]
    except Exception as exc:
        _log.debug("recut: flag cache read error: %s", exc)
        return None


async def _cache_flags(content_hash: str, flags: list[RecutFlag]) -> None:
    if os.environ.get("RECUT_CACHE_ENABLED", "true").lower() != "true":
        return

    ttl = int(os.environ.get("RECUT_CACHE_TTL", "3600"))
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl)

    # Populate L1 cache immediately (no I/O)
    _mem_cache[content_hash] = (flags, expires_at)

    try:
        from recut.storage.db import StorageClient
        from recut.storage.models import FlagCache

        now = datetime.now(UTC)
        row = FlagCache(
            content_hash=content_hash,
            flags_json=json.dumps([f.model_dump(mode="json") for f in flags]),
            created_at=now,
            expires_at=expires_at,
        )
        client = StorageClient()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.save_flag_cache, row)
    except Exception as exc:
        _log.debug("recut: flag cache write error: %s", exc)
