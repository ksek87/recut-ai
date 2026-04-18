from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Label

from recut.schema.audit import AuditRecord
from recut.schema.trace import RecutTrace, Severity


class PeekView(App):
    """Triage view showing all flagged steps from a completed peek/audit."""

    TITLE = "recut peek"
    CSS = """
    DataTable { height: 1fr; }
    Label.summary { padding: 1 2; background: $surface; }
    Label.clean { padding: 1 2; color: $success; background: $surface; }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, trace: RecutTrace, record: AuditRecord) -> None:
        super().__init__()
        self._trace = trace
        self._record = record

    def compose(self) -> ComposeResult:
        yield Header()
        flagged_count = sum(1 for s in self._trace.steps if s.flags)
        if flagged_count == 0:
            yield Label("No issues detected.", classes="clean", id="summary")
        else:
            yield Label(self._record.behavioral_summary, classes="summary", id="summary")
        yield DataTable()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Step", "Type", "Flag", "Severity", "Reason")
        table.cursor_type = "row"

        for step in self._trace.steps:
            for flag in step.flags:
                sev = flag.severity
                severity_markup = (
                    f"[red]{sev.upper()}[/red]" if sev == Severity.HIGH
                    else f"[yellow]{sev.upper()}[/yellow]" if sev == Severity.MEDIUM
                    else sev.upper()
                )
                table.add_row(
                    str(step.index),
                    str(step.type),
                    str(flag.type),
                    severity_markup,
                    flag.plain_reason[:72],
                )
