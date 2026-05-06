"""Layer 4: batched LLM judge — BYOM (bring your own model) backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import anthropic
import openai

from recut.flagging.flags import Thresholds
from recut.flagging.prompts import BATCH_FLAGGING_PROMPT, FLAGGING_SYSTEM_PROMPT
from recut.providers._utils import get_api_timeout
from recut.schema.trace import FlagSource, FlagType, RecutFlag, RecutStep, Severity

_log = logging.getLogger(__name__)

_L4_VALID_BACKENDS = frozenset({"local", "anthropic", "openai"})
_l4_clients: dict[str, Any] = {}

_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "local": "llama3",
}


def _get_l4_client(backend: str) -> Any:
    if backend not in _l4_clients:
        timeout = get_api_timeout()
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


async def _layer4_llm_judge(steps: list[RecutStep], original_prompt: str) -> list[RecutFlag]:
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
    """Parse structured per-flag JSON from the LLM judge into RecutFlag objects."""
    try:
        results = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("recut: Layer 4 returned non-JSON (%.80s…): %s", raw, exc)
        return []

    if not isinstance(results, list):
        _log.warning("recut: Layer 4 response is not a JSON array")
        return []

    flags: list[RecutFlag] = []
    thresholds = Thresholds()

    for result in results:
        if not isinstance(result, dict):
            continue
        step_id = result.get("step_id", "")
        raw_flags = result.get("flags", [])
        if not isinstance(raw_flags, list):
            continue

        for entry in raw_flags:
            if not isinstance(entry, dict):
                continue
            score = entry.get("score", 0.0)
            if not isinstance(score, (int, float)) or score < thresholds.LOW:
                continue

            try:
                flag_type = FlagType(entry.get("flag_type", ""))
            except ValueError:
                continue

            severity = (
                Severity.HIGH
                if score >= thresholds.HIGH
                else Severity.MEDIUM
                if score >= thresholds.MEDIUM
                else Severity.LOW
            )

            raw_confidence = entry.get("confidence")
            confidence = (
                float(max(0.0, min(1.0, raw_confidence)))
                if isinstance(raw_confidence, (int, float))
                else None
            )
            evidence = entry.get("evidence") or None
            if isinstance(evidence, str):
                evidence = evidence[:200].strip() or None

            flags.append(
                RecutFlag(
                    type=flag_type,
                    severity=severity,
                    plain_reason=entry.get("plain_reason")
                    or f"Flagged by meta-LLM (score {score:.2f}).",
                    step_id=step_id,
                    source=FlagSource.LLM,
                    confidence=confidence,
                    evidence=evidence,
                )
            )

    return flags
