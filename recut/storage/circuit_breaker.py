from __future__ import annotations

import os
import time

_failure_count: int = 0
_disabled_until: float = 0.0

_THRESHOLD = int(os.environ.get("RECUT_CB_THRESHOLD", 5))
_COOLDOWN = int(os.environ.get("RECUT_CB_COOLDOWN", 60))


def record_failure() -> None:
    global _failure_count, _disabled_until
    _failure_count += 1
    if _failure_count >= _THRESHOLD:
        _disabled_until = time.monotonic() + _COOLDOWN


def record_success() -> None:
    global _failure_count, _disabled_until
    _failure_count = 0
    _disabled_until = 0.0


def is_open() -> bool:
    """True means the circuit is open — writes should be skipped."""
    global _failure_count, _disabled_until
    if _disabled_until == 0.0:
        return False
    if time.monotonic() >= _disabled_until:
        _failure_count = 0
        _disabled_until = 0.0
        return False
    return True
