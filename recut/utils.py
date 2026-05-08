"""Shared utilities used across the recut package."""

from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)


def parse_float_env(key: str, default: float) -> float:
    """Read an env var as float; log a warning and return default if invalid."""
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        _log.warning("recut: invalid %s env var; using %.4g", key, default)
        return default


def parse_int_env(key: str, default: int, minimum: int | None = None) -> int:
    """Read an env var as int; log a warning and return default if invalid."""
    try:
        value = int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        _log.warning("recut: invalid %s env var; using %d", key, default)
        value = default
    return max(minimum, value) if minimum is not None else value


def parse_bool_env(key: str, default: bool) -> bool:
    """Read an env var as bool; returns default if absent or unrecognised."""
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes")


def get_context_window() -> int:
    """Return the number of preceding steps used for context in flagging/caching."""
    return parse_int_env("RECUT_CONTEXT_WINDOW_SIZE", 2, minimum=1)
