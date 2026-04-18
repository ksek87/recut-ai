from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

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
    ) -> list[RecutStep]:
        """Re-run agent from fork_index with injected content."""
        ...

    @abstractmethod
    async def run_agent(
        self,
        prompt: str,
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[RecutStep]:
        """Run the agent and yield steps as they stream in."""
        ...
