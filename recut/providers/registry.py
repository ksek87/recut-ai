from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recut.providers.base import AbstractProvider

_registry: dict[str, AbstractProvider] = {}


def register(name: str, provider: AbstractProvider) -> None:
    """Register a provider instance. Called from provider modules at import time."""
    _registry[name] = provider


def get_provider(name: str) -> AbstractProvider | None:
    """Look up a single provider by name without copying the registry."""
    return _registry.get(name)


def get_registered() -> dict[str, AbstractProvider]:
    """Return a snapshot of all currently registered providers."""
    return dict(_registry)


def load_providers() -> None:
    """Import built-in provider modules so they self-register via register()."""
    with contextlib.suppress(ImportError):
        import recut.providers.anthropic  # noqa: F401
    with contextlib.suppress(ImportError):
        import recut.providers.openai  # noqa: F401
