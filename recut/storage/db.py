from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from recut.schema.trace import RecutStep, RecutTrace, TraceLanguage, TraceMeta, TraceMode
from recut.storage.models import AuditRow, FlagCache, ForkRow, TraceRow  # noqa: F401

_engine = None


def get_db_path() -> Path:
    raw = os.environ.get("RECUT_DB_PATH", "~/.recut/recut.db")
    path = Path(raw).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_engine():
    global _engine
    if _engine is None:
        db_path = get_db_path()
        _engine = create_engine(f"sqlite:///{db_path}", echo=False)
        SQLModel.metadata.create_all(_engine)
    return _engine


def get_session() -> Session:
    return Session(get_engine())


class StorageClient:
    def __init__(self, session: Session | None = None):
        self._session = session

    def _get_session(self) -> Session:
        return self._session or get_session()

    def save_trace_row(self, row: TraceRow) -> None:
        with self._get_session() as session:
            session.merge(row)
            session.commit()

    def get_trace_row(self, trace_id: str) -> TraceRow | None:
        with self._get_session() as session:
            return session.get(TraceRow, trace_id)

    def load_trace(self, trace_id: str) -> RecutTrace | None:
        row = self.get_trace_row(trace_id)
        return self._row_to_trace(row) if row else None

    def load_recent_traces(self, agent_id: str, limit: int = 50) -> list[RecutTrace]:
        with self._get_session() as session:
            rows = session.exec(
                select(TraceRow)
                .where(TraceRow.agent_id == agent_id)
                .order_by(TraceRow.created_at.desc())
                .limit(limit)
            ).all()
        return [t for row in rows if (t := self._row_to_trace(row))]

    def _row_to_trace(self, row: TraceRow) -> RecutTrace:
        steps = [RecutStep(**s) for s in json.loads(row.steps_json)]
        return RecutTrace(
            id=row.id,
            created_at=row.created_at,
            agent_id=row.agent_id,
            prompt=row.prompt,
            mode=TraceMode(row.mode),
            language=TraceLanguage(row.language),
            meta=TraceMeta(model=row.model, provider=row.provider, total_steps=len(steps)),
            steps=steps,
        )

    def save_audit_row(self, row: AuditRow) -> None:
        with self._get_session() as session:
            session.merge(row)
            session.commit()

    def get_audit_row(self, audit_id: str) -> AuditRow | None:
        with self._get_session() as session:
            return session.get(AuditRow, audit_id)

    def save_fork_row(self, row: ForkRow) -> None:
        with self._get_session() as session:
            session.merge(row)
            session.commit()

    def get_fork_row(self, fork_id: str) -> ForkRow | None:
        with self._get_session() as session:
            return session.get(ForkRow, fork_id)

    def get_cached_flags(self, content_hash: str) -> FlagCache | None:
        with self._get_session() as session:
            row = session.get(FlagCache, content_hash)
            if row and row.expires_at > datetime.now(UTC):
                return row
            return None

    def save_flag_cache(self, row: FlagCache) -> None:
        with self._get_session() as session:
            session.merge(row)
            session.commit()
