from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(help="Full structured audit of a recorded trace.")
console = Console()


@app.callback(invoke_without_command=True)
def audit_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID to audit"),
    tui: bool = typer.Option(False, "--tui", help="Launch interactive TUI"),
) -> None:
    """Full audit — all four flagging layers, full AuditRecord output."""
    asyncio.run(_audit_async(trace_id, tui=tui))


async def _audit_async(trace_id: str, *, tui: bool = False) -> None:
    import json

    from recut.cli.tui.audit_view import AuditView
    from recut.core.auditor import audit
    from recut.schema.trace import RecutStep, RecutTrace, TraceLanguage, TraceMeta, TraceMode
    from recut.storage.db import StorageClient

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

    record = await audit(trace)

    if tui:
        AuditView(trace, record).run()
        return

    console.print(
        Panel(
            f"[bold]Summary:[/bold] {record.behavioral_summary}\n\n"
            f"[bold]Flags:[/bold] {record.flag_count} total, highest: {record.highest_severity or 'none'}\n"
            f"[bold]Status:[/bold] {record.review_status.value}",
            title=f"Audit — {trace.agent_id}",
        )
    )

    profile = record.risk_profile
    profile_dict = profile.model_dump()
    nonzero = {k: v for k, v in profile_dict.items() if v > 0}
    if nonzero:
        console.print("\n[bold]Risk profile:[/bold]")
        for k, v in nonzero.items():
            console.print(f"  {k.replace('_count', '').replace('_', ' ')}: {v}")
