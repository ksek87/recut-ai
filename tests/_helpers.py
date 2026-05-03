"""Shared test helpers imported by multiple test modules."""

from __future__ import annotations

from recut.schema.trace import RecutStep


class _StubProvider:
    model = "stub-model"

    async def capture_step(self, raw_response: dict) -> RecutStep:  # pragma: no cover
        raise NotImplementedError

    def supports_native_reasoning(self) -> bool:
        return False

    async def replay_from(
        self, steps, fork_index, injection
    ) -> list[RecutStep]:  # pragma: no cover
        raise NotImplementedError

    async def run_agent(self, prompt, system=None, tools=None):  # pragma: no cover
        raise NotImplementedError
