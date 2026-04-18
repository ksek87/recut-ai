from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Quick triage of a recorded trace.")
console = Console()


@app.callback(invoke_without_command=True)
def peek_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID to peek at"),
) -> None:
    """Fast triage — surfaces high-risk steps without a full audit."""
    asyncio.run(_peek_async(trace_id))


async def _peek_async(trace_id: str) -> None:
    from recut.storage.db import StorageClient
    from recut.schema.trace import RecutTrace, RecutStep, TraceMeta, TraceMode, TraceLanguage
    from recut.core.auditor import peek
    import json

    client = StorageClient()
    row = client.get_trace_row(trace_id)
    if not row:
        console.print(f"[red]Trace not found:[/red] {trace_id}")
        raise typer.Exit(1)

    steps = [RecutStep(**s) for s in json.loads(row.steps_json)]
    trace = RecutTrace(
        id=row.id,
        created_at=row.created_at,
        agent_id=row.agent_id,
        prompt=row.prompt,
        mode=TraceMode(row.mode),
        language=TraceLanguage(row.language),
        meta=TraceMeta(model=row.model, provider=row.provider, total_steps=len(steps)),
        steps=steps,
    )

    record = await peek(trace)

    console.print(f"\n[bold]Peek:[/bold] {record.behavioral_summary}")

    flagged = [s for s in trace.steps if s.flags]
    if not flagged:
        console.print("[green]No issues detected.[/green]")
        return

    table = Table(title="Flagged Steps", show_lines=True)
    table.add_column("Step", style="dim")
    table.add_column("Type")
    table.add_column("Flag")
    table.add_column("Severity")
    table.add_column("Reason")

    for step in flagged:
        for flag in step.flags:
            table.add_row(
                str(step.index),
                step.type.value,
                flag.type.value,
                f"[red]{flag.severity.value}[/red]" if flag.severity.value == "high"
                else f"[yellow]{flag.severity.value}[/yellow]",
                flag.plain_reason[:80],
            )

    console.print(table)
