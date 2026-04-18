from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Stress test a trace with auto-generated variants.")
console = Console()


@app.callback(invoke_without_command=True)
def stress_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID to stress test"),
    variants: int = typer.Option(3, "--variants", "-n", help="Number of variants to generate"),
) -> None:
    """Generate stress variants from flagged steps and compare outcomes."""
    asyncio.run(_stress_async(trace_id, variants))


async def _stress_async(trace_id: str, num_variants: int) -> None:
    from recut.storage.db import StorageClient
    from recut.schema.trace import RecutTrace, RecutStep, TraceMeta, TraceMode, TraceLanguage
    from recut.providers.anthropic import AnthropicProvider
    from recut.core.stress import stress
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

    provider = AnthropicProvider()
    console.print(f"Running {num_variants} stress variant(s)...")

    runs = await stress(trace, provider, num_variants=num_variants)

    if not runs:
        console.print("[yellow]No flagged steps to stress test.[/yellow]")
        return

    table = Table(title="Stress Results", show_lines=True)
    table.add_column("Variant")
    table.add_column("Strategy")
    table.add_column("Verdict")
    table.add_column("Risk Delta")
    table.add_column("Summary")

    for run in runs:
        verdict_style = {
            "stable": "[green]stable[/green]",
            "degraded": "[yellow]degraded[/yellow]",
            "failed": "[red]failed[/red]",
        }.get(run.verdict.value, run.verdict.value)

        table.add_row(
            str(run.variant_index),
            run.injection_strategy.value,
            verdict_style,
            f"{run.risk_delta:+.3f}",
            run.plain_summary[:60],
        )

    console.print(table)
