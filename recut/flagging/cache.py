"""In-process + SQLite-backed flag cache shared across all flagging layers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta

from recut.schema.trace import RecutFlag, RecutStep
from recut.storage.db import StorageClient
from recut.storage.models import FlagCache
from recut.utils import get_context_window, parse_int_env

_log = logging.getLogger(__name__)

# L1 in-memory cache: content_hash -> (flags, expires_at)
_mem_cache: dict[str, tuple[list[RecutFlag], datetime]] = {}


def _cache_key(step: RecutStep, preceding: list[RecutStep]) -> str:
    window = get_context_window()
    context = step.content + "".join(p.content for p in preceding[-window:])
    return hashlib.sha256(context.encode()).hexdigest()


async def _get_cached_flags(content_hash: str) -> list[RecutFlag] | None:
    if os.environ.get("RECUT_CACHE_ENABLED", "true").lower() != "true":
        return None

    entry = _mem_cache.get(content_hash)
    if entry is not None:
        flags, expires_at = entry
        if datetime.now(UTC) < expires_at:
            return flags
        del _mem_cache[content_hash]

    try:
        client = StorageClient()
        loop = asyncio.get_running_loop()
        row = await loop.run_in_executor(None, client.get_cached_flags, content_hash)
        if row is None:
            return None
        return [RecutFlag(**f) for f in json.loads(row.flags_json)]
    except Exception as exc:
        _log.debug("recut: flag cache read error: %s", exc)
        return None


async def _cache_flags(content_hash: str, flags: list[RecutFlag]) -> None:
    if os.environ.get("RECUT_CACHE_ENABLED", "true").lower() != "true":
        return

    ttl = parse_int_env("RECUT_CACHE_TTL", 3600, minimum=1)
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
    _mem_cache[content_hash] = (flags, expires_at)

    try:
        row = FlagCache(
            content_hash=content_hash,
            flags_json=json.dumps([f.model_dump(mode="json") for f in flags]),
            created_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        client = StorageClient()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.save_flag_cache, row)
    except Exception as exc:
        _log.debug("recut: flag cache write error: %s", exc)
