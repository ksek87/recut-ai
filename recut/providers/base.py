from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from recut.schema.trace import RecutStep


class AbstractProvider(ABC):
    @abstractmethod
    async def capture_step(self, raw_response: dict) -> RecutStep:
        """Parse a raw LLM response into a RecutStep."""
        ...

    @abstractmethod
    def supports_native_reasoning(self) -> bool:
        """True if provider exposes real reasoning tokens."""
        ...

    @abstractmethod
    async def replay_from(
        self,
        steps: list[RecutStep],
        fork_index: int,
        injection: dict,
        prompt: str = "",
    ) -> list[RecutStep]:
        """Re-run agent from fork_index with injected content.

        ``prompt`` is the original user prompt that started the trace, so
        providers can reconstruct the full conversation as context.
        """
        ...

    @abstractmethod
    def run_agent(
        self,
        prompt: str,
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[RecutStep]:
        """Run the agent and yield steps as they stream in."""
        ...
