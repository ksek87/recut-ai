"""PII scrubbing before storage. Opt-in via RECUT_PII_SCRUB=true.

Enabled patterns (comma-separated) via RECUT_PII_PATTERNS:
  email, phone, ssn, credit_card, ip_address (default: all)

All matched values are replaced with [REDACTED].
"""

from __future__ import annotations

import os
import re

_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"\b(\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[\s\-]?){3}\d{4}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}

_PLACEHOLDER = "[REDACTED]"
_ALL_PATTERN_NAMES = frozenset(_PATTERNS)


def _enabled_patterns() -> frozenset[str]:
    raw = os.environ.get("RECUT_PII_PATTERNS", ",".join(_ALL_PATTERN_NAMES))
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def is_enabled() -> bool:
    return os.environ.get("RECUT_PII_SCRUB", "false").lower() == "true"


def scrub(text: str) -> str:
    """Return text with PII replaced by [REDACTED]. No-op if RECUT_PII_SCRUB != true."""
    if not is_enabled() or not text:
        return text
    enabled = _enabled_patterns()
    for name, pattern in _PATTERNS.items():
        if name in enabled:
            text = pattern.sub(_PLACEHOLDER, text)
    return text


def scrub_steps(steps_data: list[dict]) -> list[dict]:
    """Scrub content and reasoning fields in a serialised steps list (in-place)."""
    if not is_enabled():
        return steps_data
    for step in steps_data:
        if "content" in step:
            step["content"] = scrub(step["content"])
        reasoning = step.get("reasoning")
        if isinstance(reasoning, dict) and "content" in reasoning:
            reasoning["content"] = scrub(reasoning["content"])
    return steps_data
