from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console

app = typer.Typer(help="Replay a trace from a fork point.")
console = Console()


@app.callback(invoke_without_command=True)
def replay_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID to replay"),
    step: int = typer.Option(..., "--step", "-s", help="Step index to fork from"),
    inject: str = typer.Option(
        ..., "--inject", "-i", help="JSON injection: {target, injected_content}"
    ),
    tui: bool = typer.Option(False, "--tui", help="Launch interactive diff TUI after replay"),
) -> None:
    """Fork a trace at a step and replay from there with an injection."""
    asyncio.run(_replay_async(trace_id, step, inject, tui=tui))


async def _replay_async(
    trace_id: str, step_index: int, inject_json: str, *, tui: bool = False
) -> None:
    from recut.cli.tui.diff_view import DiffView
    from recut.core.replayer import replay
    from recut.providers.anthropic import AnthropicProvider
    from recut.schema.fork import ForkInjection, InjectionTarget
    from recut.schema.trace import RecutStep, RecutTrace, TraceLanguage, TraceMeta, TraceMode
    from recut.storage.db import StorageClient

    client = StorageClient()
    row = client.get_trace_row(trace_id)
    if not row:
        console.print(f"[red]Trace not found:[/red] {trace_id}")
        raise typer.Exit(1)

    steps_data = json.loads(row.steps_json)
    steps = [RecutStep(**s) for s in steps_data]

    trace = RecutTrace(
        id=row.id,
        created_at=row.created_at,
        agent_id=row.agent_id,
        prompt=row.prompt,
        mode=TraceMode(row.mode),
        language=TraceLanguage(row.language),
        meta=TraceMeta(model=row.model, provider=row.provider),
        steps=steps,
    )

    try:
        inject_data = json.loads(inject_json)
    except json.JSONDecodeError:
        console.print("[red]Invalid JSON for --inject[/red]")
        raise typer.Exit(1) from None

    injection = ForkInjection(
        target=InjectionTarget(inject_data.get("target", "tool_result")),
        original_content=steps[step_index].content if step_index < len(steps) else "",
        injected_content=inject_data.get("injected_content", ""),
    )

    provider = AnthropicProvider()
    fork = await replay(
        trace=trace, fork_step_index=step_index, injection=injection, provider=provider
    )

    if tui:
        DiffView(trace, fork).run()
        return

    console.print(f"\n[green]Fork created:[/green] {fork.id}")
    if fork.diff:
        console.print(f"[bold]Diff:[/bold] {fork.diff.plain_summary}")
        console.print(f"[bold]Risk delta:[/bold] {fork.diff.risk_delta:+.3f}")
