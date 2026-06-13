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
        self, steps, fork_index, injection, prompt=""
    ) -> list[RecutStep]:  # pragma: no cover
        raise NotImplementedError

    async def run_agent(self, prompt, system=None, tools=None):  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def patch_target(cls) -> tuple[type, str]:  # pragma: no cover
        raise NotImplementedError

    def parse_response(self, response: object, model: str = "unknown") -> list[RecutStep]:  # pragma: no cover
        raise NotImplementedError

    def build_messages(self, steps: list, injection: dict, prompt: str = "") -> list[dict]:  # pragma: no cover
        raise NotImplementedError
