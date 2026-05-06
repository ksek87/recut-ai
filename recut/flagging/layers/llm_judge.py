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
from recut.utils import parse_float_env, parse_int_env

_log = logging.getLogger(__name__)

_L4_VALID_BACKENDS = frozenset({"local", "anthropic", "openai"})
_l4_clients: dict[str, Any] = {}

_BUILTIN_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "local": "llama3",
}


def _default_model(backend: str) -> str:
    """Return the default model for the given backend, respecting per-backend env overrides."""
    env_key = f"RECUT_META_MODEL_{backend.upper()}"
    return os.environ.get(env_key, _BUILTIN_DEFAULT_MODELS[backend])


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
    max_tokens = parse_int_env("RECUT_L4_MAX_TOKENS", 2000, minimum=1)
    if backend == "anthropic":
        response = await client.messages.create(
            model=meta_model,
            max_tokens=max_tokens,
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
            max_tokens=max_tokens,
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

    meta_model = os.environ.get("RECUT_META_MODEL") or _default_model(backend)

    content_truncate = parse_int_env("RECUT_L4_CONTENT_TRUNCATE", 500, minimum=1)
    reasoning_truncate = parse_int_env("RECUT_L4_REASONING_TRUNCATE", 300, minimum=1)
    prompt_truncate = parse_int_env("RECUT_L4_PROMPT_TRUNCATE", 300, minimum=1)

    steps_payload = json.dumps(
        [
            {
                "step_id": s.id,
                "index": s.index,
                "type": s.type.value,
                "content": s.content[:content_truncate],
                "reasoning": s.reasoning.content[:reasoning_truncate] if s.reasoning else None,
            }
            for s in steps
        ],
        indent=2,
    )

    prompt = BATCH_FLAGGING_PROMPT.format(
        prompt=original_prompt[:prompt_truncate],
        steps_json=steps_payload,
    )

    retry_attempts = parse_int_env("RECUT_L4_RETRY_ATTEMPTS", 3, minimum=1)
    ratelimit_backoff = parse_float_env("RECUT_L4_RATELIMIT_BACKOFF", 5.0)
    connection_backoff = parse_float_env("RECUT_L4_CONNECTION_BACKOFF", 2.0)

    raw = ""
    for attempt in range(retry_attempts):
        try:
            raw = await _call_l4_api(backend, FLAGGING_SYSTEM_PROMPT, prompt, meta_model)
            return _parse_llm_flags(raw, steps)
        except (anthropic.AuthenticationError, openai.AuthenticationError) as exc:
            _log.warning("recut: Layer 4 auth error (backend=%r): %s", backend, exc)
            return []
        except (anthropic.RateLimitError, openai.RateLimitError):
            if attempt < retry_attempts - 1:
                await asyncio.sleep(ratelimit_backoff * (attempt + 1))
            else:
                _log.warning(
                    "recut: Layer 4 rate-limited after %d attempts, skipping", retry_attempts
                )
                return []
        except (anthropic.APIConnectionError, openai.APIConnectionError):
            if backend == "local":
                _log.debug("recut: L4 local backend unreachable, skipping")
                return []
            if attempt < retry_attempts - 1:
                await asyncio.sleep(connection_backoff**attempt)
            else:
                _log.warning(
                    "recut: Layer 4 connection error after %d attempts, skipping", retry_attempts
                )
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
    evidence_truncate = parse_int_env("RECUT_L4_EVIDENCE_TRUNCATE", 200, minimum=1)

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
                evidence = evidence[:evidence_truncate].strip() or None

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
