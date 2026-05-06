"""Shared provider utilities."""

from __future__ import annotations

import httpx

from recut.utils import parse_float_env


def get_api_timeout() -> httpx.Timeout:
    return httpx.Timeout(parse_float_env("RECUT_API_TIMEOUT", 60.0), connect=10.0)
