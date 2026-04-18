"""Textual TUI — live peek queue view. Phase 4 implementation."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Label
from textual.reactive import reactive

from recut.schema.trace import RecutTrace, Severity


class PeekView(App):
    """Live triage view showing flagged steps as they arrive."""

    TITLE = "recut peek"
    CSS = """
    DataTable { height: 1fr; }
    Label.summary { padding: 1; background: $surface; }
    """

    trace: reactive[RecutTrace | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Waiting for trace...", classes="summary", id="summary")
        yield DataTable()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Step", "Type", "Flag", "Severity", "Reason")

    def add_step(self, step) -> None:
        table = self.query_one(DataTable)
        for flag in step.flags:
            severity_str = flag.severity.value.upper()
            style = "red" if flag.severity == Severity.HIGH else "yellow" if flag.severity == Severity.MEDIUM else "white"
            table.add_row(
                str(step.index),
                step.type.value,
                flag.type.value,
                f"[{style}]{severity_str}[/{style}]",
                flag.plain_reason[:60],
            )

    def update_summary(self, text: str) -> None:
        self.query_one("#summary", Label).update(text)
