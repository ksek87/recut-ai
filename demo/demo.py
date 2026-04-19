"""
recut-ai SDK demo — Multi-step Research Agent

A real multi-turn Claude agent with tool use, traced and flagged by recut-ai.
Requires ANTHROPIC_API_KEY (falls back to MockProvider if absent).

Run:
    ANTHROPIC_API_KEY=sk-ant-... python demo/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from recut.export.exporter import export
from recut.flagging.engine import FlaggingEngine
from recut.schema.trace import (
    ReasoningSource,
    RecutStep,
    RecutTrace,
    Severity,
    StepReasoning,
    StepType,
    TraceMeta,
    TraceMode,
)

console = Console()

DEMO_PROMPT = (
    "Analyse NVIDIA (NVDA) as an investment opportunity. "
    "Search for its current price, P/E ratio, revenue growth, analyst consensus, "
    "and key risks. Also compare it to competitors in the AI chip space. "
    "Provide a structured recommendation."
)

# ---------------------------------------------------------------------------
# Tool definitions + hardcoded responses (no external API needed)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_financial_data",
        "description": "Look up financial metrics for a stock ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "e.g. NVDA"},
                "metric": {
                    "type": "string",
                    "enum": ["price", "pe_ratio", "revenue_growth", "analyst_consensus", "risks"],
                },
            },
            "required": ["ticker", "metric"],
        },
    },
    {
        "name": "compare_competitors",
        "description": "Compare a company to its main competitors on a given criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "criteria": {
                    "type": "string",
                    "enum": ["market_share", "valuation", "growth"],
                },
            },
            "required": ["company", "criteria"],
        },
    },
]

_TOOL_DATA: dict[str, dict] = {
    "search_financial_data": {
        ("NVDA", "price"): "NVDA: $875/share. +187% YTD. Market cap $2.15T.",
        (
            "NVDA",
            "pe_ratio",
        ): "NVDA P/E: 65x. Sector median: 28x. Premium reflects AI growth expectations.",
        ("NVDA", "revenue_growth"): "Revenue +122% YoY ($44B TTM). Data center: 87% of revenue.",
        ("NVDA", "analyst_consensus"): "Strong Buy. 38/42 analysts bullish. Avg. target: $1,050.",
        ("NVDA", "risks"): (
            "Key risks: China export controls, AMD competition, valuation multiple "
            "contraction, customer concentration (Microsoft/Google/Meta = 40% of revenue)."
        ),
    },
    "compare_competitors": {
        (
            "NVDA",
            "market_share",
        ): "NVDA: 80%+ data center GPU market share. AMD: ~15% (MI300X improving). Intel Gaudi: <5%.",
        ("NVDA", "valuation"): "NVDA: 65x P/E. AMD: 45x. Intel: 25x (declining earnings).",
        ("NVDA", "growth"): "NVDA: +122% YoY revenue. AMD: +18%. Intel: -1%.",
    },
}


def _execute_tool(name: str, inputs: dict) -> str:
    lookup = _TOOL_DATA.get(name, {})
    if name == "search_financial_data":
        key = (inputs.get("ticker", "").upper(), inputs.get("metric", ""))
    elif name == "compare_competitors":
        key = (inputs.get("company", "").upper(), inputs.get("criteria", ""))
    else:
        return f"Unknown tool: {name}"
    return lookup.get(key, f"No data for {key}.")


# ---------------------------------------------------------------------------
# Real agentic loop (Anthropic API)
# ---------------------------------------------------------------------------


async def _run_real_agent(prompt: str) -> tuple[list[RecutStep], str]:
    """Multi-turn tool-calling loop. Returns (steps, provider_model)."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("RECUT_DEMO_MODEL", "claude-sonnet-4-6")
    client = anthropic.AsyncAnthropic(api_key=api_key)

    messages: list[dict] = [{"role": "user", "content": prompt}]
    all_steps: list[RecutStep] = []
    step_index = 0

    for _turn in range(6):
        response = await client.messages.create(
            model=model,
            max_tokens=8000,
            thinking={"type": "enabled", "budget_tokens": 5000},
            tools=TOOLS,
            messages=messages,
        )

        pending_reasoning: StepReasoning | None = None
        tool_uses = []
        assistant_content = list(response.content)

        for block in response.content:
            if block.type == "thinking":
                pending_reasoning = StepReasoning(
                    source=ReasoningSource.NATIVE,
                    content=block.thinking,
                    confidence=1.0,
                )
                all_steps.append(
                    RecutStep(
                        index=step_index,
                        type=StepType.REASONING,
                        content=block.thinking,
                        reasoning=pending_reasoning,
                    )
                )
                step_index += 1

            elif block.type == "text" and block.text.strip():
                all_steps.append(
                    RecutStep(
                        index=step_index,
                        type=StepType.OUTPUT,
                        content=block.text,
                        reasoning=pending_reasoning,
                    )
                )
                pending_reasoning = None
                step_index += 1

            elif block.type == "tool_use":
                all_steps.append(
                    RecutStep(
                        index=step_index,
                        type=StepType.TOOL_CALL,
                        content=json.dumps({"name": block.name, "input": block.input}),
                        reasoning=pending_reasoning,
                    )
                )
                pending_reasoning = None
                step_index += 1
                tool_uses.append(block)

        if response.stop_reason != "tool_use" or not tool_uses:
            break

        messages.append({"role": "assistant", "content": assistant_content})
        tool_results = []
        for tu in tool_uses:
            result = _execute_tool(tu.name, tu.input)
            all_steps.append(RecutStep(index=step_index, type=StepType.TOOL_RESULT, content=result))
            step_index += 1
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
        messages.append({"role": "user", "content": tool_results})

    return all_steps, model


async def _run_mock_agent(prompt: str) -> tuple[list[RecutStep], str]:
    """Offline fallback using MockProvider."""
    from demo.mock_provider import MockProvider

    provider = MockProvider()
    steps: list[RecutStep] = []
    stream = await provider.run_agent(prompt)
    async for step in stream:
        steps.append(step)
    return steps, "mock-provider-v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_trace(steps: list[RecutStep], model: str, prompt: str) -> RecutTrace:
    return RecutTrace(
        agent_id="demo-research-agent",
        prompt=prompt,
        mode=TraceMode.PEEK,
        meta=TraceMeta(
            model=model,
            provider="AnthropicProvider" if model != "mock-provider-v1" else "MockProvider",
            total_steps=len(steps),
        ),
        steps=steps,
    )


def _print_phase(n: int, title: str) -> None:
    console.print()
    console.rule(f"[bold cyan]Phase {n} — {title}[/bold cyan]")


def _print_flags(flags_by_step: dict[str, list], steps: list[RecutStep]) -> None:
    idx_map = {s.id: s.index for s in steps}
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=4)
    table.add_column("Severity", width=8)
    table.add_column("Type", width=30)
    table.add_column("Source", width=10)
    table.add_column("Reason")

    for step_id, flags in flags_by_step.items():
        for flag in flags:
            colour = {"high": "red", "medium": "yellow", "low": "green"}.get(
                flag.severity.value, "white"
            )
            table.add_row(
                str(idx_map.get(step_id, "?")),
                f"[{colour}]{flag.severity.value.upper()}[/{colour}]",
                flag.type.value,
                flag.source.value,
                flag.plain_reason[:90],
            )
    console.print(table)


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


async def phase1_run(use_real: bool) -> tuple[list[RecutStep], RecutTrace]:
    _print_phase(1, "Run Agent")
    if use_real:
        console.print(
            f"[dim]Calling Claude API (model: {os.environ.get('RECUT_DEMO_MODEL', 'claude-sonnet-4-6')})...[/dim]"
        )
    else:
        console.print("[yellow]ANTHROPIC_API_KEY not set — using MockProvider.[/yellow]")

    steps, model = (
        await _run_real_agent(DEMO_PROMPT) if use_real else await _run_mock_agent(DEMO_PROMPT)
    )

    for step in steps:
        preview = step.content[:70].replace("\n", " ")
        console.print(
            f"  step {step.index}: [bold]{step.type.value}[/bold] — {preview}{'…' if len(step.content) > 70 else ''}"
        )

    trace = _build_trace(steps, model, DEMO_PROMPT)
    console.print(f"\n[green]Trace:[/green] {trace.id}  ({len(steps)} steps, model={model})")
    return steps, trace


async def phase2_flag(steps: list[RecutStep]) -> dict[str, list]:
    _print_phase(2, "Flag Scoring (peek)")
    engine = FlaggingEngine(mode=TraceMode.PEEK, use_embeddings=False, use_llm_judge=False)
    flags_by_step = await engine.score_batch(steps, DEMO_PROMPT)

    total = sum(len(v) for v in flags_by_step.values())
    high = sum(1 for fl in flags_by_step.values() for f in fl if f.severity == Severity.HIGH)
    console.print(f"[green]Flags:[/green] {total} total, [red]{high} HIGH[/red]")
    if total:
        _print_flags(flags_by_step, steps)
    else:
        console.print("  [dim]No flags raised — agent behaviour looks clean.[/dim]")
    return flags_by_step


def phase3_export(trace: RecutTrace) -> Path:
    _print_phase(3, "Export")
    out = Path("demo") / "demo_trace.recut.json"
    result = export(trace, output_path=out)
    console.print(f"[green]Written:[/green] {result}  ({result.stat().st_size / 1024:.1f} KB)")
    return result


def phase4_otel(
    trace: RecutTrace,
    steps: list[RecutStep],
    flags_by_step: dict[str, list],
) -> None:
    _print_phase(4, "OpenTelemetry Spans")
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    try:
        from demo.otel_bridge import emit_trace, setup_otel

        tp = setup_otel("recut-demo", endpoint=endpoint)
        backend = f"OTLP → {endpoint}" if endpoint else "ConsoleSpanExporter"
        console.print(f"[dim]Backend:[/dim] {backend}")
        emit_trace(trace, steps, flags_by_step)
        tp.force_flush()
        console.print("[green]Spans emitted.[/green]")
        if not endpoint:
            console.print(
                Panel(
                    "To visualise in Jaeger:\n"
                    "  docker run -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one\n"
                    "  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python demo/demo.py",
                    border_style="dim",
                )
            )
    except ImportError:
        console.print(
            "[yellow]opentelemetry-sdk not installed.[/yellow] Run: pip install -e '.[demo]'"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    use_real = bool(os.environ.get("ANTHROPIC_API_KEY"))

    console.print(
        Panel(
            "[bold]recut-ai SDK Demo[/bold] — Multi-step Research Agent\n\n"
            + (
                "Using real Claude API with extended thinking + tool use."
                if use_real
                else "[yellow]Offline mode — set ANTHROPIC_API_KEY for a real run.[/yellow]"
            ),
            border_style="cyan",
        )
    )

    steps, trace = await phase1_run(use_real)
    flags_by_step = await phase2_flag(steps)
    phase3_export(trace)
    phase4_otel(trace, steps, flags_by_step)

    console.print()
    console.print(Panel("[bold green]Demo complete.[/bold green]", border_style="green"))


if __name__ == "__main__":
    asyncio.run(main())
