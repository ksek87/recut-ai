from __future__ import annotations

import asyncio

import typer
from rich.console import Console

app = typer.Typer(help="Export a trace to .recut.json.")
console = Console()


@app.callback(invoke_without_command=True)
def export_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID to export"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output path"),
) -> None:
    """Export a trace (and any linked audit) to a portable .recut.json file."""
    asyncio.run(_export_async(trace_id, output))


async def _export_async(trace_id: str, output: str | None) -> None:
    from recut.export.exporter import export
    from recut.storage.db import StorageClient

    client = StorageClient()
    trace = client.load_trace(trace_id)
    if not trace:
        console.print(f"[red]Trace not found:[/red] {trace_id}")
        raise typer.Exit(1)

    path = export(trace, output_path=output)
    console.print(f"[green]Exported:[/green] {path}")
