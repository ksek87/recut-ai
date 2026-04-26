from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Markdown

from recut.schema.audit import AuditRecord
from recut.schema.trace import FlagSource, RecutTrace, Severity

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

_SOURCE_LABEL: dict[FlagSource, str] = {
    FlagSource.RULE: "[rule]",
    FlagSource.EMBEDDING: "[embedding]",
    FlagSource.NATIVE: "[native]",
    FlagSource.LLM: "[judge]",
}


class AuditView(App):
    """Full walkthrough view for a completed AuditRecord."""

    TITLE = "recut audit"
    CSS = """
    Markdown { height: auto; padding: 1 2; background: $surface; }
    DataTable { height: 1fr; }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, trace: RecutTrace, record: AuditRecord) -> None:
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
        table.add_columns("Step", "Type", "Risk", "Flags", "Source", "Summary")
        table.cursor_type = "row"

        for step in self._trace.steps:
            risk_str = f"{step.risk_score:.2f}"
            risk_markup = (
                f"[red]{risk_str}[/red]"
                if step.risk_score >= 0.8
                else f"[yellow]{risk_str}[/yellow]"
                if step.risk_score >= 0.5
                else risk_str
            )
            flag_count = len(step.flags)
            highest = max(
                (f.severity for f in step.flags),
                key=lambda s: _SEVERITY_ORDER.get(s, 0),
                default=None,
            )
            flag_cell = (
                f"[red]{flag_count}[/red]"
                if highest == Severity.HIGH
                else f"[yellow]{flag_count}[/yellow]"
                if highest == Severity.MEDIUM
                else str(flag_count)
            )
            sources = {_SOURCE_LABEL.get(f.source, str(f.source)) for f in step.flags}
            source_cell = " ".join(sorted(sources)) if sources else ""
            table.add_row(
                str(step.index),
                str(step.type),
                risk_markup,
                flag_cell,
                source_cell,
                step.plain_summary[:60],
            )

    def _build_summary_md(self) -> str:
        r = self._record
        profile = r.risk_profile.model_dump()
        nonzero = {
            k.replace("_count", "").replace("_", " "): v for k, v in profile.items() if v > 0
        }
        profile_lines = "\n".join(f"- **{k}**: {v}" for k, v in nonzero.items()) or "_none_"

        from recut.providers._pricing import format_cost

        total_cost = sum(s.token_cost for s in self._trace.steps if s.token_cost)
        cost_line = f"  |  **Cost:** {format_cost(total_cost)}" if total_cost else ""

        return (
            f"## {self._trace.agent_id} — audit\n\n"
            f"{r.behavioral_summary}\n\n"
            f"**Flags:** {r.flag_count}  |  "
            f"**Status:** {r.review_status}  |  "
            f"**Highest severity:** {r.highest_severity or 'none'}{cost_line}\n\n"
            f"### Risk profile\n\n{profile_lines}"
        )
