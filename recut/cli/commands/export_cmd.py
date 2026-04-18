from __future__ import annotations

import asyncio
import json
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(help="Export a trace to .recut.json.")
console = Console()


@app.callback(invoke_without_command=True)
def export_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID to export"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path"),
) -> None:
    """Export a trace (and any linked audit) to a portable .recut.json file."""
    asyncio.run(_export_async(trace_id, output))


async def _export_async(trace_id: str, output: Optional[str]) -> None:
    from recut.storage.db import StorageClient
    from recut.schema.trace import RecutTrace, RecutStep, TraceMeta, TraceMode, TraceLanguage
    from recut.export.exporter import export

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

    path = export(trace, output_path=output)
    console.print(f"[green]Exported:[/green] {path}")
