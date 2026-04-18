from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from recut.schema.audit import AuditRecord
from recut.schema.fork import RecutFork
from recut.schema.trace import RecutTrace


def export(
    trace: RecutTrace,
    audit_record: Optional[AuditRecord] = None,
    forks: Optional[list[RecutFork]] = None,
    output_path: Optional[str | Path] = None,
) -> Path:
    """
    Export a trace (and optionally its audit record and forks) to a .recut.json file.

    Returns the path to the written file.
    """
    payload = _build_payload(trace, audit_record, forks)

    if output_path is None:
        output_path = Path(f"{trace.id}.recut.json")
    else:
        output_path = Path(output_path)

    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output_path


def _build_payload(
    trace: RecutTrace,
    audit_record: Optional[AuditRecord],
    forks: Optional[list[RecutFork]],
) -> dict:
    payload: dict = {
        "recut_version": "0.1.0",
        "exported_at": datetime.utcnow().isoformat(),
        "trace": trace.model_dump(mode="json"),
    }
    if audit_record:
        payload["audit"] = audit_record.model_dump(mode="json")
    if forks:
        payload["forks"] = [f.model_dump(mode="json") for f in forks]
    return payload


def load_export(path: str | Path) -> dict:
    """Load a .recut.json file back into a dict."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
