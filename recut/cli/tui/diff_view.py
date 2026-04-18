"""Textual TUI — side-by-side fork diff view. Phase 4 implementation."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Label

from recut.schema.fork import RecutFork
from recut.schema.trace import RecutStep, RecutTrace


class DiffView(App):
    """Side-by-side view comparing original trace steps vs forked replay."""

    TITLE = "recut diff"
    CSS = """
    Horizontal { height: 1fr; }
    DataTable { width: 1fr; }
    Label.panel-title { background: $surface; padding: 1; text-align: center; }
    """

    def __init__(self, trace: RecutTrace, fork: RecutFork):
        super().__init__()
        self._trace = trace
        self._fork = fork
        self._replayed = [RecutStep(**s) for s in fork.replay_steps]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id="original")
            yield DataTable(id="replayed")
        if self._fork.diff:
            yield Label(self._fork.diff.plain_summary, classes="panel-title")
        yield Footer()

    def on_mount(self) -> None:
        original_table = self.query_one("#original", DataTable)
        replayed_table = self.query_one("#replayed", DataTable)

        for table in (original_table, replayed_table):
            table.add_columns("Step", "Type", "Risk", "Content")

        for step in self._trace.steps[self._fork.fork_step_index:]:
            original_table.add_row(
                str(step.index), step.type.value, f"{step.risk_score:.2f}", step.content[:40]
            )

        for step in self._replayed:
            replayed_table.add_row(
                str(step.index), step.type.value, f"{step.risk_score:.2f}", step.content[:40]
            )
