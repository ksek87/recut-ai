"""Textual TUI views. Requires the optional [tui] extra (pip install 'recut-ai[tui]')."""

from __future__ import annotations

import typer
from rich.console import Console

try:
    from recut.cli.tui.audit_view import AuditView
    from recut.cli.tui.diff_view import DiffView
    from recut.cli.tui.peek_view import PeekView
except ImportError:
    AuditView = DiffView = PeekView = None  # type: ignore[assignment,misc]

__all__ = ["AuditView", "DiffView", "PeekView", "require_tui"]


def require_tui(view_cls: type | None, console: Console) -> type:
    """Return the view class, or exit with install guidance if textual is missing."""
    if view_cls is None:
        console.print("[red]TUI requires: pip install 'recut-ai[tui]'[/red]")
        raise typer.Exit(1)
    return view_cls
