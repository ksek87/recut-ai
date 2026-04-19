from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(help="Run an agent with tracing.", invoke_without_command=True)
console = Console()


@app.callback(invoke_without_command=True)
def run(
    prompt: str = typer.Argument(..., help="The prompt to send to the agent"),
    agent: str = typer.Option("default", "--agent", "-a", help="Agent ID"),
    mode: str = typer.Option("peek", "--mode", "-m", help="intercept | peek | audit"),
    language: str = typer.Option("simple", "--language", "-l", help="simple | power"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", help="Model to use"),
) -> None:
    """Run an agent and trace the execution."""
    asyncio.run(_run_async(prompt, agent, mode, language, model))


async def _run_async(prompt: str, agent_id: str, mode: str, language: str, model: str) -> None:
    from recut.core.auditor import audit, peek
    from recut.core.tracer import trace_context
    from recut.plain.summariser import summarise_step
    from recut.providers.anthropic import AnthropicProvider
    from recut.schema.trace import TraceMode

    provider = AnthropicProvider(model=model)

    try:
        _mode = TraceMode(mode)
    except ValueError:
        console.print(f"[red]Unknown mode: {mode}. Use peek, audit, or intercept.[/red]")
        raise typer.Exit(1) from None

    console.print(
        Panel(
            f"[bold]Agent:[/bold] {agent_id}  [bold]Mode:[/bold] {mode}\n[bold]Prompt:[/bold] {prompt}",
            title="recut run",
        )
    )

    async with trace_context(agent_id=agent_id, mode=_mode, provider=provider) as ctx:
        ctx.trace.prompt = prompt

        async for step in provider.run_agent(prompt):
            ctx.add_step(step)
            summary = summarise_step(step, ctx.trace.language)
            console.print(f"  [dim]step {step.index}[/dim] {summary}")

    trace = ctx.trace
    console.print(f"\n[green]Trace saved:[/green] {trace.id}")

    if mode in ("peek", "audit"):
        record = await (audit if mode == "audit" else peek)(trace)
        console.print(f"[bold]Summary:[/bold] {record.behavioral_summary}")
        if record.flag_count:
            console.print(
                f"[yellow]Flags:[/yellow] {record.flag_count} ({record.highest_severity} severity)"
            )
