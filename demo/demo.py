"""
recut-ai SDK demo — Multi-step Research Agent

A real multi-turn Claude agent with tool use, traced and flagged by recut-ai.
Requires ANTHROPIC_API_KEY (falls back to MockProvider if absent).

Connected mode:  real yfinance + DuckDuckGo data
Offline mode:    canned responses, no external calls

Run:
    echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
    python demo/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import recut
from recut.export.exporter import export
from recut.schema.audit import AuditRecord
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
    "Look up its current price, P/E ratio, revenue growth, and analyst consensus. "
    "Search the web for any recent risks or concerns. "
    "Compare it to competitors in the AI chip space. "
    "Provide a structured Buy / Hold / Sell recommendation with reasoning."
)

# ---------------------------------------------------------------------------
# Global flag handler — demonstrates @recut.on_flag
# Registered once at import time; fires in peek, audit, and intercept modes.
# ---------------------------------------------------------------------------


@recut.on_flag(severity="high")
def _on_high_flag(event: recut.RecutFlagEvent) -> None:
    """Print a real-time alert whenever a HIGH-severity flag fires."""
    source_label = {
        "rule": "[dim]\\[rule][/dim]",
        "embedding": "[dim]\\[embedding][/dim]",
        "native": "[bold yellow]\\[native][/bold yellow]",
        "llm": "[cyan]\\[judge][/cyan]",
    }.get(event.flag.source.value, event.flag.source.value)
    console.print(
        f"  [bold red]⚡ HIGH[/bold red] {event.flag.type.value} "
        f"{source_label}  [dim]{event.flag.plain_reason[:80]}[/dim]"
    )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "get_stock_data",
        "description": (
            "Look up financial metrics for a stock ticker. "
            "Returns real-time data from Yahoo Finance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "e.g. NVDA, AMD"},
                "metric": {
                    "type": "string",
                    "enum": ["price", "pe_ratio", "revenue_growth", "analyst_consensus"],
                    "description": "Which metric to retrieve.",
                },
            },
            "required": ["ticker", "metric"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for current news and analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
]

# ---------------------------------------------------------------------------
# Real tool executors (yfinance + DuckDuckGo)
# ---------------------------------------------------------------------------

_yf_cache: dict[str, dict] = {}  # ticker → info dict; avoids repeated network calls


def _ticker_info(ticker: str) -> dict:
    key = ticker.upper()
    if key not in _yf_cache:
        import yfinance as yf

        _yf_cache[key] = yf.Ticker(key).info
    return _yf_cache[key]


def _real_get_stock_data(ticker: str, metric: str) -> str:
    info = _ticker_info(ticker)

    if metric == "price":
        price = (
            info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        )
        change_52w = (info.get("52WeekChange") or 0) * 100
        mktcap = info.get("marketCap", 0) / 1e12
        return f"{ticker.upper()}: ${price:,.2f}. 52-week change: {change_52w:+.1f}%. Market cap: ${mktcap:.2f}T."

    if metric == "pe_ratio":
        pe = info.get("trailingPE")
        fwd_pe = info.get("forwardPE")
        return (
            f"{ticker.upper()} trailing P/E: {pe:.1f}x. Forward P/E: {fwd_pe:.1f}x."
            if pe and fwd_pe
            else f"{ticker.upper()} P/E data unavailable."
        )

    if metric == "revenue_growth":
        growth = (info.get("revenueGrowth") or 0) * 100
        revenue = (info.get("totalRevenue") or 0) / 1e9
        gross_margin = (info.get("grossMargins") or 0) * 100
        return (
            f"{ticker.upper()} TTM revenue: ${revenue:.1f}B. "
            f"YoY growth: {growth:+.1f}%. Gross margin: {gross_margin:.1f}%."
        )

    if metric == "analyst_consensus":
        rec = (info.get("recommendationKey") or "n/a").replace("_", " ").title()
        target = info.get("targetMeanPrice")
        low = info.get("targetLowPrice")
        high = info.get("targetHighPrice")
        n = info.get("numberOfAnalystOpinions") or "N/A"
        target_str = f"${target:.0f} (range ${low:.0f}–${high:.0f})" if target else "N/A"
        return f"{ticker.upper()} consensus: {rec}. Mean target: {target_str}. Analysts: {n}."

    return f"Unknown metric: {metric}"


def _real_web_search(query: str) -> str:
    from duckduckgo_search import DDGS

    results = list(DDGS().text(query, max_results=4))
    if not results:
        return "No results found."
    return "\n".join(f"• {r['title']}: {r['body'][:200]}" for r in results)


def _execute_tool_real(name: str, inputs: dict) -> str:
    try:
        if name == "get_stock_data":
            return _real_get_stock_data(inputs["ticker"], inputs["metric"])
        if name == "web_search":
            return _real_web_search(inputs["query"])
    except Exception as exc:
        return f"Tool error ({name}): {exc}"
    return f"Unknown tool: {name}"


async def _execute_tools_parallel(tool_uses: list, executor: object | None = None) -> list[str]:
    """Execute all tool calls from one API turn concurrently."""
    loop = asyncio.get_running_loop()
    return list(
        await asyncio.gather(
            *[loop.run_in_executor(None, _execute_tool_real, tu.name, tu.input) for tu in tool_uses]
        )
    )


# ---------------------------------------------------------------------------
# Canned tool responses (offline / mock mode only)
# ---------------------------------------------------------------------------

_CANNED_STOCK: dict[str, str] = {
    "NVDA|price": "NVDA: $875/share. +187% YTD. Market cap $2.15T.",
    "NVDA|pe_ratio": "NVDA trailing P/E: 65.0x. Forward P/E: 38.0x.",
    "NVDA|revenue_growth": "NVDA TTM revenue: $44.1B. YoY growth: +122.4%. Gross margin: 74.6%.",
    "NVDA|analyst_consensus": "NVDA consensus: Strong Buy. Mean target: $1,050 (range $700–$1,400). Analysts: 42.",
    "AMD|price": "AMD: $165/share. +12% YTD. Market cap $267B.",
    "AMD|pe_ratio": "AMD trailing P/E: 280x. Forward P/E: 28x.",
}

_CANNED_WEB: dict[str, str] = {
    "risk": "• China export controls restrict H100/H200 sales.\n• AMD MI300X gaining enterprise adoption.\n• Customer concentration: Microsoft/Google/Meta = ~40% of revenue.",
    "competitor": "• AMD MI300X closing the gap in memory bandwidth for LLM inference.\n• Intel Gaudi 3 targeting enterprise at lower price points.\n• Custom silicon (Google TPU, AWS Trainium) reducing hyperscaler GPU spend.",
}


def _execute_tool_mock(name: str, inputs: dict) -> str:
    if name == "get_stock_data":
        key = f"{inputs.get('ticker', '').upper()}|{inputs.get('metric', '')}"
        return _CANNED_STOCK.get(key, f"No cached data for {key}.")
    if name == "web_search":
        query = inputs.get("query", "").lower()
        for keyword, result in _CANNED_WEB.items():
            if keyword in query:
                return result
        return "No cached results for this query."
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Agentic loops
# ---------------------------------------------------------------------------


async def _run_real_agent(prompt: str) -> tuple[list[RecutStep], str]:
    """Multi-turn tool-calling loop against the real Anthropic API."""
    import anthropic

    model = os.environ.get("RECUT_DEMO_MODEL", "claude-sonnet-4-6")
    client = anthropic.AsyncAnthropic()

    messages: list[dict] = [{"role": "user", "content": prompt}]
    all_steps: list[RecutStep] = []
    step_index = 0

    for _turn in range(8):
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
        results = await _execute_tools_parallel(tool_uses)
        tool_results = []
        for tu, result in zip(tool_uses, results, strict=True):
            console.print(f"  [dim]  ↳ {tu.name}({_fmt_inputs(tu.input)}) → {result[:80]}…[/dim]")
            all_steps.append(RecutStep(index=step_index, type=StepType.TOOL_RESULT, content=result))
            step_index += 1
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
        messages.append({"role": "user", "content": tool_results})

    return all_steps, model


async def _run_mock_agent(prompt: str) -> tuple[list[RecutStep], str]:
    """Offline fallback: scripted MockProvider steps + canned tool responses."""
    from demo.mock_provider import MockProvider

    provider = MockProvider()
    steps: list[RecutStep] = []
    stream = await provider.run_agent(prompt)
    async for step in stream:
        steps.append(step)
    return steps, "mock-provider-v1"


def _fmt_inputs(inputs: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in inputs.items())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Source label rendering matches the recut CLI output style
_SOURCE_LABEL: dict[str, str] = {
    "rule": "[dim]\\[rule][/dim]",
    "embedding": "[dim]\\[embedding][/dim]",
    "native": "[bold yellow]\\[native][/bold yellow]",
    "llm": "[cyan]\\[judge][/cyan]",
}


def _build_trace(steps: list[RecutStep], model: str, prompt: str) -> RecutTrace:
    provider = "AnthropicProvider" if model != "mock-provider-v1" else "MockProvider"
    return RecutTrace(
        agent_id="demo-research-agent",
        prompt=prompt,
        mode=TraceMode.PEEK,
        meta=TraceMeta(model=model, provider=provider, total_steps=len(steps)),
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
    table.add_column("Layer", width=12)
    table.add_column("Reason")
    for step_id, flags in flags_by_step.items():
        for flag in flags:
            colour = {"high": "red", "medium": "yellow", "low": "green"}.get(
                flag.severity.value, "white"
            )
            source_label = _SOURCE_LABEL.get(flag.source.value, flag.source.value)
            table.add_row(
                str(idx_map.get(step_id, "?")),
                f"[{colour}]{flag.severity.value.upper()}[/{colour}]",
                flag.type.value,
                source_label,
                flag.plain_reason[:90],
            )
    console.print(table)


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


async def phase1_run(use_real: bool) -> tuple[list[RecutStep], RecutTrace]:
    _print_phase(1, "Run Agent")
    if use_real:
        model_name = os.environ.get("RECUT_DEMO_MODEL", "claude-sonnet-4-6")
        console.print(f"[dim]Model: {model_name} | Tools: yfinance + DuckDuckGo[/dim]")
    else:
        console.print(
            "[yellow]ANTHROPIC_API_KEY not set — offline mode (MockProvider + canned data).[/yellow]"
        )

    steps, model = (
        await _run_real_agent(DEMO_PROMPT) if use_real else await _run_mock_agent(DEMO_PROMPT)
    )

    for step in steps:
        preview = step.content[:70].replace("\n", " ")
        console.print(
            f"  step {step.index}: [bold]{step.type.value}[/bold]"
            f" — {preview}{'…' if len(step.content) > 70 else ''}"
        )

    trace = _build_trace(steps, model, DEMO_PROMPT)
    console.print(f"\n[green]Trace:[/green] {trace.id}  ({len(steps)} steps)")
    return steps, trace


async def phase2_peek(trace: RecutTrace) -> dict[str, list]:
    """Score all steps via recut.peek() — uses flagging_depth='fast' (layers 1-3, zero LLM cost).

    HIGH flags fire the @recut.on_flag handler registered at module level in real time.
    To enable the Layer 4 LLM judge: recut.peek(trace, flagging_depth="full").
    Layer 4 defaults to a local model (RECUT_L4_BACKEND=local); set RECUT_L4_BACKEND=anthropic
    to use the Anthropic API instead.
    """
    _print_phase(2, "Flag Scoring — recut.peek(flagging_depth='fast')")
    console.print(
        "[dim]Layers 1-3: rules + native thinking analysis. "
        "HIGH flags fire @recut.on_flag handler.[/dim]"
    )

    audit_record: AuditRecord = await recut.peek(trace, flagging_depth="fast")

    flags_by_step = {s.id: s.flags for s in trace.steps if s.flags}
    total = sum(len(v) for v in flags_by_step.values())
    high = sum(1 for fl in flags_by_step.values() for f in fl if f.severity == Severity.HIGH)

    cost_str = ""
    if trace.meta.token_cost is not None:
        unit = os.environ.get("RECUT_COST_UNIT", "USD")
        cost_str = f"  |  [dim]cost: {trace.meta.token_cost:.4f} {unit}[/dim]"

    console.print(
        f"[green]Flags:[/green] {total} total, [red]{high} HIGH[/red]"
        f"  |  risk: {audit_record.highest_severity or 'none'}"
        f"{cost_str}"
    )

    if total:
        _print_flags(flags_by_step, trace.steps)
    else:
        console.print("  [dim]No flags raised.[/dim]")
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
    load_dotenv()
    use_real = bool(os.environ.get("ANTHROPIC_API_KEY"))

    l4_backend = os.environ.get("RECUT_L4_BACKEND", "local")
    l4_note = f"Layer 4 judge: [dim]{l4_backend}[/dim]" + (
        " (skipped in fast mode)" if l4_backend == "local" else ""
    )

    console.print(
        Panel(
            f"[bold]recut-ai {recut.__version__} SDK Demo[/bold] — Multi-step Research Agent\n\n"
            + (
                "Connected: real Claude API + live yfinance & DuckDuckGo data."
                if use_real
                else "[yellow]Offline: set ANTHROPIC_API_KEY for a live run.[/yellow]"
            )
            + f"\n[dim]{l4_note}[/dim]",
            border_style="cyan",
        )
    )

    steps, trace = await phase1_run(use_real)
    flags_by_step = await phase2_peek(trace)
    phase3_export(trace)
    phase4_otel(trace, steps, flags_by_step)

    console.print()
    console.print(Panel("[bold green]Demo complete.[/bold green]", border_style="green"))


if __name__ == "__main__":
    asyncio.run(main())
