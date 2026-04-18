"""Textual TUI — full audit walkthrough view. Phase 4 implementation."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Label, Markdown
from textual.reactive import reactive

from recut.schema.audit import AuditRecord
from recut.schema.trace import RecutTrace


class AuditView(App):
    """Walkthrough view for a completed AuditRecord."""

    TITLE = "recut audit"
    CSS = """
    Markdown { height: auto; padding: 1; }
    DataTable { height: 1fr; }
    """

    def __init__(self, trace: RecutTrace, record: AuditRecord):
        super().__init__()
        self._trace = trace
        self._record = record

    def compose(self) -> ComposeResult:
        yield Header()
        yield Markdown(self._build_summary_md())
        yield DataTable()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Step", "Type", "Risk", "Flags", "Summary")
        for step in self._trace.steps:
            table.add_row(
                str(step.index),
                step.type.value,
                f"{step.risk_score:.2f}",
                str(len(step.flags)),
                step.plain_summary[:60],
            )

    def _build_summary_md(self) -> str:
        r = self._record
        return (
            f"## Audit — {self._trace.agent_id}\n\n"
            f"{r.behavioral_summary}\n\n"
            f"**Flags:** {r.flag_count} | **Status:** {r.review_status.value} | "
            f"**Highest severity:** {r.highest_severity or 'none'}"
        )
