from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from recut.schema.hooks import FlagHandler, RecutFlagEvent

_log = logging.getLogger(__name__)

# Registry: list of (handler, filters) where filters may contain "severity" and "flag_type"
_registry: list[tuple[FlagHandler, dict[str, Any]]] = []


def register(
    fn: FlagHandler,
    *,
    severity: str | None = None,
    flag_type: str | None = None,
) -> None:
    _registry.append((fn, {"severity": severity, "flag_type": flag_type}))


def get_all() -> list[tuple[FlagHandler, dict[str, Any]]]:
    return list(_registry)


def has_handlers() -> bool:
    return bool(_registry)


def matches(event: RecutFlagEvent, filters: dict[str, Any]) -> bool:
    sev = filters.get("severity")
    ft = filters.get("flag_type")
    return (not sev or event.flag.severity.value == sev) and (not ft or event.flag.type.value == ft)


async def fire_all(event: RecutFlagEvent) -> None:
    """Fire all registered global handlers whose filters match the event."""
    coros: list[Coroutine[Any, Any, Any]] = []
    for handler, filters in list(_registry):  # snapshot to allow mutation during iteration
        if matches(event, filters):
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    coros.append(result)
            except Exception as exc:
                _log.warning("recut: on_flag handler raised: %s", exc)
    if coros:
        await asyncio.gather(*coros, return_exceptions=True)
