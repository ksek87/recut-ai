from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from recut.core.checker import CheckError, check_agent
from recut.schema.check import CheckReport

app = typer.Typer(help="CI regression gate — fail when agent behavior regresses.")
console = Console()


@app.callback(invoke_without_command=True)
def check_cmd(
    agent: str = typer.Option(..., "--agent", "-a", help="Agent ID to check"),
    baseline: str | None = typer.Option(
        None, "--baseline", "-b", help="Baseline trace ID (defaults to stored baseline)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> None:
    """
    Compare the agent's most recent trace against its baseline.

    Exits 0 when all checks pass, 1 when any check regresses. On first run
    (no baseline yet), stores the current trace as baseline and exits 0.
    """
    asyncio.run(_check_async(agent, baseline, json_output=json_output))


async def _check_async(agent: str, baseline: str | None, *, json_output: bool = False) -> None:
    try:
        report = await check_agent(agent, baseline_id=baseline)
    except CheckError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    if json_output:
        print(report.model_dump_json(indent=2))
    else:
        _print_report(report)

    if not report.passed:
        raise typer.Exit(1)


def _print_report(report: CheckReport) -> None:
    if report.first_run:
        console.print(
            f"[yellow]No baseline for '{report.agent_id}' — stored trace "
            f"{report.trace_id} as baseline. All future runs are checked against it.[/yellow]"
        )
        return

    table = Table(title=f"recut check — {report.agent_id}")
    table.add_column("check")
    table.add_column("status")
    table.add_column("value", justify="right")
    table.add_column("threshold", justify="right")
    table.add_column("detail")

    for c in report.checks:
        status = "[green]pass[/green]" if c.passed else "[red]FAIL[/red]"
        table.add_row(c.name, status, f"{c.value:g}", f"{c.threshold:g}", c.detail)

    console.print(table)
    verdict = "[green]PASSED[/green]" if report.passed else "[red]FAILED[/red]"
    console.print(f"\n{verdict}  trace={report.trace_id}  baseline={report.baseline_trace_id}")
