"""
recut-ai SDK end-to-end demo — "Biased Research Agent"

Walks through all major SDK features in 7 phases:
  1. Build trace   — run MockProvider, collect steps
  2. peek          — score flags, print summary
  3. intercept     — live flag callback during a second run
  4. replay        — inject a corrected step at the duplicate tool call
  5. stress        — run N variations and compare flag counts
  6. export        — write demo_trace.recut.json
  7. OTel          — emit spans to ConsoleSpanExporter

Run:
    python demo/demo.py

No ANTHROPIC_API_KEY required.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Allow running from the repo root without installing
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from demo.mock_provider import MockProvider
from recut.export.exporter import export
from recut.flagging.engine import FlaggingEngine
from recut.schema.trace import (
    RecutStep,
    RecutTrace,
    Severity,
    StepType,
    TraceMeta,
    TraceMode,
)

console = Console()

DEMO_PROMPT = "Analyse NVDA's 2024 stock performance and provide a clear investment recommendation."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_trace(steps: list[RecutStep]) -> RecutTrace:
    return RecutTrace(
        agent_id="demo-biased-research-agent",
        prompt=DEMO_PROMPT,
        mode=TraceMode.PEEK,
        meta=TraceMeta(
            model="mock-provider-v1",
            provider="MockProvider",
            duration_seconds=1.2,
            total_steps=len(steps),
        ),
        steps=steps,
    )


def _print_phase(n: int, title: str) -> None:
    console.print()
    console.rule(f"[bold cyan]Phase {n} — {title}[/bold cyan]")


def _print_flags(flags_by_step: dict[str, list]) -> None:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Step", style="dim", width=6)
    table.add_column("Severity", width=8)
    table.add_column("Type", width=30)
    table.add_column("Source", width=10)
    table.add_column("Reason")

    # Build step_id → index map
    for step_id, flags in flags_by_step.items():
        for flag in flags:
            colour = {"high": "red", "medium": "yellow", "low": "green"}.get(
                flag.severity.value, "white"
            )
            table.add_row(
                step_id[:8],
                f"[{colour}]{flag.severity.value.upper()}[/{colour}]",
                flag.type.value,
                flag.source.value,
                flag.plain_reason[:80],
            )
    console.print(table)


# ---------------------------------------------------------------------------
# Phase 1 — Build trace
# ---------------------------------------------------------------------------


async def phase1_build_trace(provider: MockProvider) -> tuple[list[RecutStep], RecutTrace]:
    _print_phase(1, "Build Trace")
    console.print("[dim]Running MockProvider scripted 6-step sequence...[/dim]")

    steps: list[RecutStep] = []
    stream = await provider.run_agent(DEMO_PROMPT)
    async for step in stream:
        steps.append(step)
        console.print(
            f"  step {step.index}: [bold]{step.type.value}[/bold] "
            f"— {step.content[:60]}{'…' if len(step.content) > 60 else ''}"
        )

    trace = _build_trace(steps)
    console.print(f"\n[green]Trace built:[/green] {trace.id}  ({len(steps)} steps)")
    return steps, trace


# ---------------------------------------------------------------------------
# Phase 2 — peek (score flags)
# ---------------------------------------------------------------------------


async def phase2_peek(
    steps: list[RecutStep],
) -> dict[str, list]:
    _print_phase(2, "peek() — Flag Scoring")
    engine = FlaggingEngine(mode=TraceMode.PEEK, use_embeddings=False, use_llm_judge=False)
    flags_by_step = await engine.score_batch(steps, DEMO_PROMPT)

    total = sum(len(v) for v in flags_by_step.values())
    high = sum(1 for flags in flags_by_step.values() for f in flags if f.severity == Severity.HIGH)
    console.print(f"[green]Flags raised:[/green] {total} total, [red]{high} HIGH[/red]")
    _print_flags(flags_by_step)
    return flags_by_step


# ---------------------------------------------------------------------------
# Phase 3 — intercept (live callback)
# ---------------------------------------------------------------------------


async def phase3_intercept(provider: MockProvider) -> None:
    _print_phase(3, "intercept() — Live Flag Callback")
    console.print("[dim]Re-running agent with a per-step flag callback...[/dim]")

    engine = FlaggingEngine(mode=TraceMode.INTERCEPT, use_embeddings=False, use_llm_judge=False)
    collected: list[RecutStep] = []

    stream = await provider.run_agent(DEMO_PROMPT)
    async for step in stream:
        collected.append(step)
        preceding = collected[max(0, len(collected) - 3) : len(collected) - 1]
        flags = await engine.score_step(step, preceding, DEMO_PROMPT)
        if flags:
            for f in flags:
                console.print(
                    f"  [red]FLAG[/red] step {step.index}: "
                    f"[bold]{f.type.value}[/bold] ({f.severity.value})"
                )

    console.print("[green]Intercept run complete.[/green]")


# ---------------------------------------------------------------------------
# Phase 4 — replay (inject corrected step)
# ---------------------------------------------------------------------------


async def phase4_replay(
    provider: MockProvider,
    steps: list[RecutStep],
) -> list[RecutStep]:
    _print_phase(4, "replay() — Inject Corrected Step at Fork Index 3")

    injection = {
        "type": StepType.TOOL_CALL,
        "content": json.dumps(
            {"tool": "web_search", "query": "NVDA stock risks and analyst downgrades 2024"}
        ),
    }
    console.print(f"[dim]Injecting:[/dim] {injection['content'][:80]}")

    replayed = await provider.replay_from(steps, fork_index=3, injection=injection)
    engine = FlaggingEngine(mode=TraceMode.REPLAY, use_embeddings=False, use_llm_judge=False)
    replay_flags = await engine.score_batch(replayed, DEMO_PROMPT)

    total = sum(len(v) for v in replay_flags.values())
    console.print(
        f"[green]Replay complete.[/green] {len(replayed)} steps, {total} flag(s) after correction."
    )
    return replayed


# ---------------------------------------------------------------------------
# Phase 5 — stress (N variations)
# ---------------------------------------------------------------------------


async def phase5_stress(provider: MockProvider) -> None:
    _print_phase(5, "stress() — N Variations")

    engine = FlaggingEngine(mode=TraceMode.STRESS, use_embeddings=False, use_llm_judge=False)
    n_runs = 3
    console.print(f"[dim]Running {n_runs} variations (same script, different trace IDs)...[/dim]")

    for i in range(n_runs):
        steps: list[RecutStep] = []
        stream = await provider.run_agent(DEMO_PROMPT)
        async for step in stream:
            steps.append(step)

        flags = await engine.score_batch(steps, DEMO_PROMPT)
        total = sum(len(v) for v in flags.values())
        console.print(f"  Variation {i + 1}: {total} flag(s)")

    console.print("[green]Stress test complete.[/green]")


# ---------------------------------------------------------------------------
# Phase 6 — export
# ---------------------------------------------------------------------------


def phase6_export(trace: RecutTrace) -> Path:
    _print_phase(6, "export() — Write .recut.json")

    out_path = Path("demo") / "demo_trace.recut.json"
    result = export(trace, output_path=out_path)
    size_kb = result.stat().st_size / 1024
    console.print(f"[green]Exported:[/green] {result}  ({size_kb:.1f} KB)")
    return result


# ---------------------------------------------------------------------------
# Phase 7 — OTel
# ---------------------------------------------------------------------------


def phase7_otel(
    trace: RecutTrace,
    steps: list[RecutStep],
    flags_by_step: dict[str, list],
) -> None:
    _print_phase(7, "OTel — Emit Spans")

    otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    try:
        from demo.otel_bridge import emit_trace, setup_otel

        tp = setup_otel("recut-demo", endpoint=otel_endpoint)
        console.print(
            f"[dim]OTel backend:[/dim] {'OTLP → ' + otel_endpoint if otel_endpoint else 'ConsoleSpanExporter (stdout)'}"
        )
        emit_trace(trace, steps, flags_by_step)
        tp.force_flush()
        console.print("[green]Spans emitted.[/green]")
        if not otel_endpoint:
            console.print(
                Panel(
                    "Set [bold]OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317[/bold] "
                    "and run Jaeger to see spans in a UI.\n"
                    "  docker run -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one",
                    title="Tip: Use a real collector",
                    border_style="dim",
                )
            )
    except ImportError:
        console.print(
            "[yellow]opentelemetry-sdk not installed.[/yellow] "
            "Install with: pip install -e '.[demo]'"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    console.print(
        Panel(
            "[bold]recut-ai SDK Demo[/bold]\n"
            "Scenario: Biased Research Agent\n\n"
            "Expected flags:\n"
            "  • Step 3 → [red]ANOMALOUS_TOOL_USE HIGH[/red] (duplicate tool call)\n"
            "  • Step 5 → [red]REASONING_ACTION_MISMATCH HIGH[/red] (uncertain → overconfident)",
            border_style="cyan",
        )
    )

    provider = MockProvider()

    steps, trace = await phase1_build_trace(provider)
    flags_by_step = await phase2_peek(steps)
    await phase3_intercept(provider)
    await phase4_replay(provider, steps)
    await phase5_stress(provider)
    phase6_export(trace)
    phase7_otel(trace, steps, flags_by_step)

    console.print()
    console.print(Panel("[bold green]Demo complete.[/bold green]", border_style="green"))


if __name__ == "__main__":
    asyncio.run(main())
