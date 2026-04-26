from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from recut.schema.trace import FlagSource, Severity

app = typer.Typer(help="Quick triage of a recorded trace.")
console = Console()

_SOURCE_LABEL: dict[FlagSource, str] = {
    FlagSource.RULE: "[dim][rule][/dim]",
    FlagSource.EMBEDDING: "[dim][embedding][/dim]",
    FlagSource.NATIVE: "[bold][native][/bold]",
    FlagSource.LLM: "[cyan][judge][/cyan]",
}


@app.callback(invoke_without_command=True)
def peek_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID to peek at"),
    tui: bool = typer.Option(False, "--tui", help="Launch interactive TUI"),
) -> None:
    """Fast triage — surfaces high-risk steps without a full audit."""
    asyncio.run(_peek_async(trace_id, tui=tui))


async def _peek_async(trace_id: str, *, tui: bool = False) -> None:
    from recut.cli.tui.peek_view import PeekView
    from recut.core.auditor import peek
    from recut.storage.db import StorageClient

    client = StorageClient()
    trace = client.load_trace(trace_id)
    if not trace:
        console.print(f"[red]Trace not found:[/red] {trace_id}")
        raise typer.Exit(1)

    record = await peek(trace)

    if tui:
        PeekView(trace, record).run()
        return

    console.print(f"\n[bold]Peek:[/bold] {record.behavioral_summary}")

    flagged = [s for s in trace.steps if s.flags]
    if not flagged:
        console.print("[green]No issues detected.[/green]")
        return

    table = Table(title="Flagged Steps", show_lines=True)
    table.add_column("Step", style="dim")
    table.add_column("Type")
    table.add_column("Flag")
    table.add_column("Source")
    table.add_column("Severity")
    table.add_column("Reason")

    for step in flagged:
        for flag in step.flags:
            severity_cell = (
                f"[red]{flag.severity}[/red]"
                if flag.severity == Severity.HIGH
                else f"[yellow]{flag.severity}[/yellow]"
                if flag.severity == Severity.MEDIUM
                else str(flag.severity)
            )
            reason = flag.plain_reason[:80]
            if flag.confidence is not None:
                reason += f" ({flag.confidence:.0%})"
            table.add_row(
                str(step.index),
                str(step.type),
                str(flag.type),
                _SOURCE_LABEL.get(flag.source, str(flag.source)),
                severity_cell,
                reason,
            )

    console.print(table)

    total_cost = sum(s.token_cost_usd for s in trace.steps if s.token_cost_usd)
    if total_cost:
        console.print(f"[dim]Token cost: ${total_cost:.4f}[/dim]")
