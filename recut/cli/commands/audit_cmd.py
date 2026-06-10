from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel

try:
    from recut.cli.tui.audit_view import AuditView as _AuditView
except ImportError:
    _AuditView = None  # type: ignore[assignment,misc]
from recut.core.auditor import audit
from recut.storage.db import StorageClient

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
    client = StorageClient()
    trace = client.load_trace(trace_id)
    if not trace:
        console.print(f"[red]Trace not found:[/red] {trace_id}")
        raise typer.Exit(1)

    record = await audit(trace)

    if tui:
        if _AuditView is None:
            console.print("[red]TUI requires: pip install 'recut-ai[tui]'[/red]")
            raise typer.Exit(1)
        _AuditView(trace, record).run()
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

    if record.l4_judge_fires:
        confirmed = record.l4_confirmed
        fp = record.l4_false_positives
        unreviewed = record.l4_judge_fires - confirmed - fp
        console.print(
            f"\n[bold]Layer 4 calibration:[/bold] "
            f"{record.l4_judge_fires} judge flags — "
            f"{confirmed} confirmed, {fp} false positive, {unreviewed} unreviewed"
        )
