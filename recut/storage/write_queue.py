"""Async write queue — serializes DB writes to a single background worker.

All persistence calls funnel through enqueue(), which returns immediately.
The background worker processes one job at a time, preventing SQLite
write-locking under concurrent traces.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

from recut.utils import parse_float_env, parse_int_env

_log = logging.getLogger(__name__)

_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None


async def _worker(queue: asyncio.Queue) -> None:
    while True:
        job = await queue.get()
        if job is None:
            queue.task_done()
            return
        try:
            await job
        except Exception as exc:
            _log.warning("recut: async write failed: %s", exc)
        finally:
            queue.task_done()


def _get_queue() -> asyncio.Queue:
    global _queue, _worker_task
    if _queue is None or (_worker_task is not None and _worker_task.done()):
        _queue = asyncio.Queue(maxsize=parse_int_env("RECUT_WRITE_QUEUE_MAXSIZE", 0, minimum=0))
        _worker_task = asyncio.ensure_future(_worker(_queue))
    return _queue


async def enqueue(job: Awaitable[None]) -> None:
    """Enqueue a write coroutine. The caller creates the coroutine; the queue runs it."""
    _get_queue().put_nowait(job)


async def drain() -> None:
    """Drain all pending writes. Call on graceful shutdown."""
    global _queue, _worker_task
    if _queue is None:
        return
    timeout = parse_float_env("RECUT_WRITE_QUEUE_DRAIN_TIMEOUT", 30.0)
    _queue.put_nowait(None)
    try:
        await asyncio.wait_for(_queue.join(), timeout=timeout)
    except TimeoutError:
        _log.warning("recut: write queue drain timed out after %.0fs", timeout)
    _queue = None
    _worker_task = None
