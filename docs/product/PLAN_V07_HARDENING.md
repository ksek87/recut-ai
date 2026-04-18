# v0.7 — Production Hardening Plan

## Goal

Make recut safe to use in enterprise and compliance contexts. Three hard requirements:
1. Sensitive data never leaves the machine unintentionally
2. Audit records are tamper-evident
3. The system degrades gracefully under load rather than failing loud

---

## 1. PII & secret scrubber — ship first, blocks enterprise

**Why first:** Reasoning blocks and tool results can contain API keys, PII, passwords, and internal data. Without a scrubber, recut cannot be deployed in regulated environments.

**Design:** `recut/security/scrubber.py`

```python
class Scrubber:
    def scrub(self, text: str) -> str: ...
```

**Detection patterns (compiled regex, applied in order):**
| Pattern | Replacement |
|---------|-------------|
| `sk-ant-[A-Za-z0-9\-_]{40,}` | `[ANTHROPIC_KEY]` |
| `sk-[A-Za-z0-9]{48}` | `[OPENAI_KEY]` |
| AWS access key `AKIA[0-9A-Z]{16}` | `[AWS_KEY]` |
| Email addresses | `[EMAIL]` |
| Credit card numbers (Luhn-valid 13-19 digit) | `[CARD]` |
| Phone numbers (E.164 + common formats) | `[PHONE]` |
| SSN `\d{3}-\d{2}-\d{4}` | `[SSN]` |
| Bearer tokens `Bearer [A-Za-z0-9\-._~+/]+=*` | `Bearer [TOKEN]` |
| Custom patterns via `RECUT_SCRUB_PATTERNS` env var (comma-separated regex) | `[REDACTED]` |

**Integration points:**
- Applied in `_persist_trace` before writing `steps_json` to SQLite
- Applied in `export()` before writing `.recut.json`
- Applied in Layer 4 payload construction before sending to meta-LLM
- NOT applied in-memory (scrubbing only at persistence/export boundaries)

**Controls:**
```
RECUT_SCRUB_ENABLED=true          # default: true
RECUT_SCRUB_PATTERNS=regex1,regex2  # extra patterns
RECUT_STORE_NATIVE=false          # if false, native thinking blocks are not stored
RECUT_EXPORT_NATIVE=false         # if false, thinking blocks stripped from exports
RECUT_TRUNCATE_NATIVE_AT=1000     # truncate native reasoning at N chars before storing
```

**Testing:** Scrubber must have 100% unit test coverage with real-world pattern examples. No false negatives on the built-in patterns is a hard requirement.

---

## 2. Trace integrity sealing

**Why:** Compliance use cases require proof that an audit record was not modified after creation. Without this, recut's audit output cannot be used as evidence.

**Design:** `recut/security/integrity.py`

```python
def seal_trace(trace: RecutTrace) -> str:
    """Returns SHA-256 hex digest of the canonical JSON representation."""

def verify_trace(trace: RecutTrace, seal: str) -> bool:
    """Returns True if the trace has not been modified since sealing."""
```

**Canonical JSON:** `json.dumps(trace.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))` — deterministic, no whitespace.

**Storage:** Add `seal: str | None` field to `TraceRow` and `RecutTrace`. Populated by `_persist_trace` after scrubbing, before writing.

**Export:** The `.recut.json` payload includes `"seal"` and `"sealed_at"` at the top level. `load_export()` optionally verifies the seal.

**CLI:**
```
recut verify <trace-id>     # prints VALID / TAMPERED
recut export --verify       # includes verification in export
```

---

## 3. PostgreSQL storage backend

**Why:** SQLite is single-writer. Multi-process deployments (multiple agent workers writing traces) will hit lock contention.

**Design:** `recut/storage/postgres.py`

- Same `StorageClient` interface as SQLite — drop-in replacement
- Uses `asyncpg` for async queries (no SQLAlchemy overhead)
- Configured via `RECUT_DB_URL=postgresql://user:pass@host/db`
- `get_engine()` in `storage/db.py` checks for `RECUT_DB_URL` and returns the appropriate engine

**Migration:** `recut db migrate` CLI command — runs schema migrations using Alembic. Schema is identical to SQLite; only the engine changes.

**pyproject.toml:**
```toml
[project.optional-dependencies]
postgres = ["asyncpg>=0.29", "alembic>=1.13"]
```

---

## 4. Async write queue with backpressure

**Why:** High-volume deployments can produce more traces than the DB can absorb synchronously. Currently `run_in_executor` blocks a thread pool thread per write.

**Design:** `recut/storage/write_queue.py`

```python
class WriteQueue:
    def __init__(self, max_size: int = 1000): ...
    async def enqueue(self, row: TraceRow | ForkRow | AuditRow) -> None: ...
    async def start(self) -> None: ...  # background consumer
    async def stop(self) -> None: ...   # drain + shutdown
```

- `asyncio.Queue(maxsize=max_size)` — bounded queue
- Background consumer task flushes in configurable batches (`RECUT_WRITE_BATCH_SIZE=50`)
- If queue is full: drop with warning log (never block the caller). Track `recut.storage.dropped_writes` counter.
- `recut.storage.queue_depth` exposed as a metric for OTel

---

## 5. Trace retention + auto-cleanup

**Why:** SQLite grows unbounded. Without retention, long-running deployments fill disk.

**Design:** `recut/storage/retention.py`

```python
def vacuum(db_path: Path, ttl_days: int) -> int:
    """Delete traces older than ttl_days. Returns count of deleted rows."""
```

**Configuration:**
```
RECUT_TRACE_TTL_DAYS=30      # 0 = keep forever (default)
RECUT_AUTO_VACUUM=false      # if true, vacuum runs on startup
```

**CLI:**
```
recut db vacuum              # manual vacuum, prints rows deleted
recut db vacuum --dry-run    # prints what would be deleted
recut db stats               # prints trace count, disk usage, oldest trace
```

---

## 6. Trace size limits

**Why:** A misbehaving agent can produce unbounded steps and content, causing OOM or very slow SQLite writes.

**Configuration:**
```
RECUT_MAX_STEPS_PER_TRACE=500    # 0 = no limit (default)
RECUT_MAX_CONTENT_LENGTH=10000   # truncate step.content at N chars
```

**Integration:** Applied in `RecutContext.add_step()`:
- If `len(trace.steps) >= max_steps`: stop appending, set a `SCOPE_CREEP` flag on the last step
- `step.content` truncated to `RECUT_MAX_CONTENT_LENGTH` before storage (not in-memory)

---

## 7. CLI audit log

**Why:** Enterprise compliance requires a record of who ran what command.

**Design:** Append-only log at `~/.recut/audit.log`. Each line is JSON:
```json
{"ts": "2026-04-18T12:00:00Z", "cmd": "audit", "trace_id": "abc123", "user": "ksekerka"}
```

Written by a Typer `result_callback` hooked into the root app. Never raises — logging failure is silently ignored.

---

## Build order

1. **PII scrubber** — blocks all enterprise conversations, high urgency
2. **Trace size limits** — correctness issue, small change
3. **Retention + vacuum CLI** — operational necessity for any real deployment
4. **Integrity sealing** — compliance requirement, self-contained
5. **Write queue** — needed at scale, depends on OTel for metrics
6. **PostgreSQL backend** — needed for multi-process, last because it requires Alembic

---

## Test requirements

- Scrubber: 100% coverage, parametrized with real pattern examples including edge cases
- Integrity: round-trip seal/verify test, tamper detection test
- Vacuum: integration test with in-memory SQLite fixture
- Write queue: backpressure test (fill queue past max_size, verify drop + no block)
