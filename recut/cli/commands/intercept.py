from __future__ import annotations

import asyncio

import typer
from rich.console import Console

app = typer.Typer(help="Run an agent with live interception.")
console = Console()


@app.callback(invoke_without_command=True)
def intercept_cmd(
    prompt: str = typer.Argument(..., help="The prompt to send to the agent"),
    agent: str = typer.Option("default", "--agent", "-a"),
    pause_on: str | None = typer.Option(
        None, "--pause-on", help="Pause on severity: low | medium | high"
    ),
) -> None:
    """Run an agent and intercept steps in real time."""
    asyncio.run(_intercept_async(prompt, agent, pause_on))


async def _intercept_async(prompt: str, agent_id: str, pause_on: str | None) -> None:
    from recut.core.interceptor import intercept
    from recut.core.tracer import trace_context
    from recut.providers.anthropic import AnthropicProvider
    from recut.schema.hooks import RecutFlagEvent
    from recut.schema.trace import TraceMode

    provider = AnthropicProvider()

    def flag_handler(event: RecutFlagEvent) -> None:
        console.print(
            f"  [red bold]FLAG[/red bold] [{event.flag.severity.value.upper()}] "
            f"{event.flag.type.value}: {event.flag.plain_reason}"
        )

    async with trace_context(agent_id=agent_id, mode=TraceMode.INTERCEPT, provider=provider) as ctx:
        ctx.trace.prompt = prompt
        step_gen = provider.run_agent(prompt)

        async for step in intercept(
            ctx.trace, step_gen, flag_handlers=[flag_handler], pause_on_severity=pause_on
        ):
            console.print(f"  [dim]step {step.index}[/dim] {step.plain_summary}")

    console.print(f"\n[green]Trace saved:[/green] {ctx.trace.id}")
