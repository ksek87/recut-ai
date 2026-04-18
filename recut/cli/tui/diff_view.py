from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Label

from recut.schema.fork import RecutFork
from recut.schema.trace import RecutStep, RecutTrace


class DiffView(App):
    """Side-by-side view comparing original trace steps vs forked replay."""

    TITLE = "recut diff"
    CSS = """
    Horizontal { height: 1fr; }
    Vertical { width: 1fr; }
    DataTable { height: 1fr; }
    Label.panel-title {
        background: $surface;
        padding: 0 1;
        text-align: center;
        width: 1fr;
    }
    Label.diff-summary {
        padding: 1 2;
        background: $surface-darken-1;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, trace: RecutTrace, fork: RecutFork) -> None:
        super().__init__()
        self._trace = trace
        self._fork = fork
        self._replayed = [RecutStep(**s) for s in fork.replay_steps]

    def compose(self) -> ComposeResult:
        yield Header()
        if self._fork.diff:
            delta = self._fork.diff.risk_delta
            delta_markup = (
                f"[red]+{delta:.3f}[/red]" if delta > 0.05
                else f"[green]{delta:+.3f}[/green]" if delta < -0.05
                else f"{delta:+.3f}"
            )
            yield Label(
                f"{self._fork.diff.plain_summary}  (risk delta: {delta_markup})",
                classes="diff-summary",
            )
        with Horizontal():
            with Vertical():
                yield Label("Original", classes="panel-title")
                yield DataTable(id="original")
            with Vertical():
                yield Label(f"Replay (fork @ step {self._fork.fork_step_index})", classes="panel-title")
                yield DataTable(id="replayed")
        yield Footer()

    def on_mount(self) -> None:
        original_table = self.query_one("#original", DataTable)
        replayed_table = self.query_one("#replayed", DataTable)

        for table in (original_table, replayed_table):
            table.add_columns("Step", "Type", "Risk", "Content")
            table.cursor_type = "row"

        divergence = self._fork.diff.divergence_step if self._fork.diff else None

        for step in self._trace.steps[self._fork.fork_step_index:]:
            risk_str = f"{step.risk_score:.2f}"
            is_diverged = divergence is not None and step.index >= divergence
            original_table.add_row(
                str(step.index),
                str(step.type),
                f"[yellow]{risk_str}[/yellow]" if is_diverged else risk_str,
                step.content[:40],
            )

        for step in self._replayed:
            risk_str = f"{step.risk_score:.2f}"
            is_diverged = divergence is not None and step.index >= divergence
            replayed_table.add_row(
                str(step.index),
                str(step.type),
                f"[yellow]{risk_str}[/yellow]" if is_diverged else risk_str,
                step.content[:40],
            )
