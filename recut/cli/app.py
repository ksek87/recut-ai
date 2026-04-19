from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="recut",
    help="Intercept, replay, and audit your AI agent runs.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

from recut.cli.commands import (  # noqa: E402
    audit_cmd,
    export_cmd,
    intercept,
    peek_cmd,
    replay_cmd,
    run,
    stress_cmd,
)

app.add_typer(run.app, name="run")
app.add_typer(intercept.app, name="intercept")
app.add_typer(replay_cmd.app, name="replay")
app.add_typer(peek_cmd.app, name="peek")
app.add_typer(audit_cmd.app, name="audit")
app.add_typer(stress_cmd.app, name="stress")
app.add_typer(export_cmd.app, name="export")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context, version: bool = typer.Option(False, "--version", "-v", help="Show version")
) -> None:
    if version:
        from recut import __version__

        console.print(f"recut-ai v{__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


if __name__ == "__main__":
    app()
