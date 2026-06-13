from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recut.providers.base import AbstractProvider

_registry: dict[str, AbstractProvider] = {}


def register(name: str, provider: AbstractProvider) -> None:
    """Register a provider instance. Called from provider modules at import time."""
    _registry[name] = provider


def get_registered() -> dict[str, AbstractProvider]:
    """Return a snapshot of all currently registered providers."""
    return dict(_registry)
