from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from recut.schema.audit import AuditRecord
from recut.schema.fork import RecutFork
from recut.schema.trace import RecutTrace


def export(
    trace: RecutTrace,
    audit_record: AuditRecord | None = None,
    forks: list[RecutFork] | None = None,
    output_path: str | Path | None = None,
) -> Path:
    """
    Export a trace (and optionally its audit record and forks) to a .recut.json file.

    Returns the path to the written file.
    """
    payload = _build_payload(trace, audit_record, forks)

    output_path = Path(f"{trace.id}.recut.json") if output_path is None else Path(output_path)

    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output_path


def _build_payload(
    trace: RecutTrace,
    audit_record: AuditRecord | None,
    forks: list[RecutFork] | None,
) -> dict:
    payload: dict = {
        "recut_version": "0.1.0",
        "exported_at": datetime.now(UTC).isoformat(),
        "trace": trace.model_dump(mode="json"),
    }
    if audit_record:
        payload["audit"] = audit_record.model_dump(mode="json")
    if forks:
        payload["forks"] = [f.model_dump(mode="json") for f in forks]
    return payload


def load_export(path: str | Path) -> dict:
    """Load a .recut.json file back into a dict."""
    return json.loads(Path(path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]
