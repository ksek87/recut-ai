# recut-ai — Production & Enterprise Readiness

---

## The Core Guarantee

Before everything else: **recut must never degrade the agent it wraps.**

If the flagging engine is slow, the trace write fails, or recut throws an internal error — the agent continues unaffected. Every safety, cost, and reliability measure below is layered on top of this non-negotiable constraint.

---

## 1. Safety

### 1.1 PII & Secret Scrubbing

Agent traces contain everything: prompts, tool call results, tool inputs, reasoning blocks. In production, this routinely includes names, emails, API keys, passwords, internal document content, and financial data.

recut must scrub before storing or exporting.

```python
from recut.safety import Scrubber, ScrubRule

recut.configure(
    scrubber=Scrubber(
        rules=[
            ScrubRule.EMAIL,           # → [REDACTED:email]
            ScrubRule.PHONE,           # → [REDACTED:phone]
            ScrubRule.API_KEY,         # → [REDACTED:api_key]
            ScrubRule.CREDIT_CARD,     # → [REDACTED:cc]
            ScrubRule.SSN,             # → [REDACTED:ssn]
        ],
        custom_patterns=[
            (r"sk-[a-zA-Z0-9]{32,}", "[REDACTED:openai_key]"),
            (r"Bearer [a-zA-Z0-9\-._~+\/]+=*", "[REDACTED:bearer_token]"),
        ]
    )
)
```

**What gets scrubbed:**
- Step `content` fields before storage
- `StepReasoning.content` (Claude thinking blocks can contain sensitive context the model received)
- `plain_reason` flag strings
- Export payloads before they leave the process

**What is never scrubbed:**
- Structural metadata — step type, index, risk score, flag type, severity — these are safe and needed for analysis

**Implementation note:** scrubbing happens in-process before any write or export. The original unscrubbed content is never persisted. Use compiled regex patterns — this runs on every captured step and must be fast.

---

### 1.2 Reasoning Block Sensitivity

Claude's native thinking blocks are particularly sensitive. The model reasons openly, often repeating back context it received verbatim. A thinking block for an HR automation agent might contain an employee's full performance review.

```python
recut.configure(
    reasoning=ReasoningConfig(
        store_native=True,           # store thinking block content (default)
        scrub_native=True,           # apply scrubber to thinking blocks
        truncate_native_at=2000,     # cap storage per block (chars), flag if truncated
        export_native=False,         # never include raw thinking in .recut.json exports
    )
)
```

The `reasoning_action_mismatch` flag can still be detected on the truncated content — the signal comes from the opening and closing sentiment of the thinking block, not its full length.

---

### 1.3 Audit Trail Integrity

For compliance use cases, the audit record must be tamper-evident. If a trace shows the agent did X and was flagged for Y, that record must be immutable once written.

```python
class RecutTrace(BaseModel):
    ...
    content_hash: str = ""      # SHA-256 of (agent_id + prompt + steps_json)
    sealed_at: Optional[datetime] = None
```

Once a trace is sealed (on `trace_complete`), the content hash is computed and stored. Any modification to the stored trace would invalidate the hash. The CLI surfaces this:

```
$ recut audit abc123 --verify-integrity
✓ Trace abc123 — integrity verified (sealed 2026-04-18T14:32:11Z)
```

For high-compliance environments, export the sealed hash to an append-only log (S3 with Object Lock, Cloudflare R2 with WORM, etc.).

---

### 1.4 Data Residency

Traces never leave the machine unless explicitly configured to. SQLite default means all data is local. No telemetry, no callbacks home, no cloud dependency.

```
RECUT_DB_PATH=~/.recut/recut.db           # default — local only
RECUT_DB_URL=postgresql://...             # opt-in remote storage
RECUT_EXPORT_ALLOWED=false               # block all export commands
RECUT_INTEGRATION_ALLOWLIST=langfuse     # whitelist which integrations can receive data
```

For air-gapped environments: recut works fully offline. The only external calls are the agent's own LLM API calls (which recut doesn't touch) and optional meta-LLM flagging calls, which can be pointed at a local Ollama instance.

```
RECUT_META_MODEL=ollama/llama3    # zero external flagging cost, fully local
```

---

## 2. Cost Efficiency

The flagging engine is the only part of recut that makes external API calls. Everything else — tracing, storage, TUI, export — is free.

### 2.1 Layered Flagging (Already Designed)

The four-layer architecture ensures the expensive layer (LLM judging) is only reached when cheaper layers don't produce a signal. In practice:

- **~40-50%** of flags are caught by Layer 1 (rule-based, free)
- **~20-30%** more by Layer 2 (embedding similarity, ~1/100th the cost of completion)
- **~10-15%** by Layer 3 (native mismatch for Claude, free)
- Only **~10-20%** of steps reach Layer 4 (batched LLM judging)

Intercept and Peek mode never reach Layer 4 by design.

---

### 2.2 Sampling

High-volume agents don't need every run traced. Sample intelligently:

```python
# Trace 10% of runs randomly
@recut.trace(agent_id="my-agent", sample_rate=0.1)

# Trace only when something interesting happens
@recut.trace(agent_id="my-agent", trace_if=lambda ctx: ctx.risk_score > 0.6)

# Trace the first run of each hour (for baseline monitoring)
@recut.trace(agent_id="my-agent", trace_if=lambda ctx: ctx.is_first_in_window("1h"))
```

```
RECUT_DEFAULT_SAMPLE_RATE=1.0    # 1.0 = always (dev default)
                                  # 0.1 = 10% (recommended for prod)
```

---

### 2.3 Flag Caching

Identical step content produces identical flags. Cache the result.

```
RECUT_CACHE_ENABLED=true
RECUT_CACHE_TTL=3600              # 1 hour default
```

Cache key: `SHA-256(step_content + preceding_2_steps_content + model_id)`

Hit rate in stress mode (many variants sharing early steps): typically 60-80%. Hit rate in high-volume identical-prompt production runs: similarly high.

Cache stored in SQLite alongside traces — no external cache dependency.

---

### 2.4 Token Budget Enforcement

Set a hard cap on meta-LLM tokens per session or per trace. When the budget is reached, recut falls back to layers 1-3 only.

```
RECUT_TOKEN_BUDGET=50000          # tokens per session (0 = no limit)
RECUT_TOKEN_BUDGET_TRACE=5000     # tokens per individual trace
```

```python
# Programmatic budget
@recut.trace(agent_id="my-agent", token_budget=2000)
```

TUI footer shows live spend: `Flagging: ~1,240 tokens used this session (~$0.0002)`

---

### 2.5 Cost Estimation Before Audit

Before running a full audit (which may invoke Layer 4), estimate the cost:

```
$ recut audit abc123 --dry-run
Trace abc123: 24 steps, 8 tool calls, 3 outputs
Estimated flagging cost: ~3,200 tokens (~$0.0005)
Layer 4 calls needed: 6 (3 tool calls above threshold, 3 outputs)
Run with: recut audit abc123
```

---

### 2.6 Batching Layer 4 Calls

When Layer 4 is invoked, batch multiple steps into a single prompt rather than one call per step. Reduces Layer 4 cost by ~70-80%.

```python
# Instead of:
# step 3 → LLM call → flags
# step 7 → LLM call → flags
# step 11 → LLM call → flags

# Batch:
# [step 3, step 7, step 11] → single LLM call → flags for all three
```

Batch size capped at 8 steps to stay within context limits. Batching is transparent — the API surface doesn't change.

---

## 3. Reliability

### 3.1 Non-Blocking Guarantee

recut's capture, flagging, and storage run in a background async task. The decorated function returns as soon as the agent completes. recut work continues concurrently.

```python
@recut.trace(agent_id="my-agent")
async def run_agent(prompt: str) -> str:
    result = await call_llm(prompt)
    return result
    # ↑ returns here at agent speed
    # recut continues writing + flagging in background
```

If the background task falls behind (slow storage write, flagging queue backup), it drops steps before blocking the agent. Dropped steps are logged with a warning — the trace is marked `partial`.

---

### 3.2 Circuit Breaker

If the flagging engine or storage layer fails repeatedly, recut trips a circuit breaker and disables itself for a configurable window. The agent runs unobserved rather than degraded.

```
RECUT_CIRCUIT_BREAKER_THRESHOLD=5     # consecutive failures before trip
RECUT_CIRCUIT_BREAKER_WINDOW=300      # seconds before retry (default 5 min)
```

Circuit breaker state is logged and surfaced in the TUI status bar. Operators can reset manually: `recut reset-circuit-breaker`.

---

### 3.3 Graceful Degradation Hierarchy

When components fail, recut falls back gracefully:

```
Storage write fails       → buffer in memory, retry with backoff, mark trace partial
Layer 4 (LLM) unavailable → fall back to layers 1-3, log warning
Layer 2 (embeddings) slow → skip, proceed to layer 3, log metric
Export destination down   → queue export locally, retry on next recut export
Integration adapter fails → log error, continue, don't surface to agent caller
```

No failure in recut ever propagates to the calling application.

---

### 3.4 Write Queue & Backpressure

High-volume agents can generate steps faster than SQLite can write them. Use an async write queue with bounded size:

```
RECUT_WRITE_QUEUE_SIZE=1000     # max queued writes before dropping (default)
RECUT_WRITE_QUEUE_TIMEOUT=5.0   # seconds before a write is dropped
```

Queue depth is exposed as a metric for monitoring: `recut.write_queue.depth`.

---

## 4. Scale

### 4.1 Storage Backends

SQLite is the default — zero config, works everywhere, sufficient for single-process agents up to millions of traces.

For multi-process, multi-host, or high-write-volume deployments:

```
RECUT_DB_URL=postgresql://user:pass@host:5432/recut    # PostgreSQL
RECUT_DB_URL=sqlite+aiosqlite:////data/recut.db        # async SQLite (default)
```

The storage layer is abstracted behind `AbstractStore`. Custom backends (DynamoDB, BigQuery, ClickHouse) can be added by implementing the interface — three methods: `write_trace`, `read_trace`, `query_traces`.

---

### 4.2 Trace Size Limits

Unbounded agents (long-running, many tool calls) can produce very large traces. Enforce limits:

```
RECUT_MAX_STEPS_PER_TRACE=500      # stop capturing after N steps
RECUT_MAX_CONTENT_LENGTH=10000     # truncate step content at N chars
RECUT_MAX_REASONING_LENGTH=5000    # truncate reasoning blocks at N chars
```

When a limit is hit, the trace is marked `truncated` and the TUI warns the user. Flagging continues on captured steps — the agent is not affected.

---

### 4.3 Retention & Cleanup

```
RECUT_TRACE_TTL_DAYS=90            # auto-delete traces older than N days
RECUT_AUDIT_RECORD_TTL_DAYS=365    # keep audit records longer than raw traces
RECUT_FORK_TTL_DAYS=30             # forks expire faster than source traces
```

`recut db vacuum` — manual cleanup command, runs VACUUM on SQLite or DELETE + ANALYZE on Postgres.

---

## 5. Compliance

### 5.1 EU AI Act Alignment

Article 14 requires human oversight mechanisms for high-risk AI systems. recut's intercept mode is a direct implementation of this requirement.

| EU AI Act requirement | recut feature |
|-----------------------|---------------|
| Human oversight capability | Intercept mode — pause, inspect, redirect |
| Logging of system outputs | RecutTrace — full step-level record |
| Auditability | AuditRecord + sealed content hash |
| Transparency of reasoning | StepReasoning capture (native for Claude) |
| Human review of high-risk decisions | `on_flag` hook → pause gate before irreversible tool calls |

Export format for compliance submissions:

```
$ recut export abc123 --format compliance
→ abc123.compliance.json
  Includes: trace, audit record, flag list, integrity hash, reviewer notes
  Excludes: raw reasoning content (configurable), scrubbed PII fields
```

---

### 5.2 SOC 2 Considerations

For teams pursuing SOC 2 Type II:

- **Access control:** traces are file-system or database-scoped — use OS/DB permissions. recut does not implement its own auth (single-process library, not a service).
- **Audit logs:** all `recut` CLI commands are logged to `~/.recut/audit.log` with timestamp, user, command, and trace ID.
- **Data retention:** configurable TTLs (see §4.3).
- **Encryption at rest:** use encrypted filesystem or PostgreSQL TDE. recut stores plaintext in SQLite by default — document this clearly.
- **Scrubbing:** PII scrubber (see §1.1) must be enabled and verified before SOC 2 scope is claimed.

---

### 5.3 Reviewer Workflow

For regulated environments where a human must approve agent outputs before action is taken:

```python
@recut.on_flag
async def require_approval(event: RecutFlagEvent):
    if event.flag.severity == "high":
        # Pause the agent and wait for human approval
        approved = await approval_gateway.request(
            trace_id=event.trace_id,
            step_id=event.step_id,
            reason=event.flag.plain_reason,
            timeout=300  # 5 min before auto-reject
        )
        if not approved:
            raise recut.AgentHalted("Rejected by reviewer")
```

`AuditRecord` captures `review_status`, `reviewer`, and `review_notes` — these fields exist in the schema today and are ready to be populated by this pattern.

---

## 6. Configuration Reference (Enterprise)

```env
# Safety
RECUT_SCRUB_PII=true
RECUT_SCRUB_SECRETS=true
RECUT_EXPORT_NATIVE_REASONING=false
RECUT_EXPORT_ALLOWED=true
RECUT_INTEGRATION_ALLOWLIST=langfuse,otel     # comma-separated, empty = all allowed

# Cost
RECUT_DEFAULT_SAMPLE_RATE=0.1
RECUT_CACHE_ENABLED=true
RECUT_CACHE_TTL=3600
RECUT_TOKEN_BUDGET=50000
RECUT_META_MODEL=claude-haiku-4-5-20251001    # or ollama/llama3 for zero cost

# Reliability
RECUT_CIRCUIT_BREAKER_THRESHOLD=5
RECUT_CIRCUIT_BREAKER_WINDOW=300
RECUT_WRITE_QUEUE_SIZE=1000

# Scale
RECUT_DB_URL=postgresql://...
RECUT_MAX_STEPS_PER_TRACE=500
RECUT_TRACE_TTL_DAYS=90
RECUT_AUDIT_RECORD_TTL_DAYS=365

# Compliance
RECUT_AUDIT_LOG=~/.recut/audit.log
RECUT_SEAL_TRACES=true                        # compute + store content hash on complete
```

---

## Open Questions for v1.0

- **Multi-tenant isolation:** if recut is embedded in a SaaS product (not just a dev tool), traces from different customers must be isolated at the storage layer. Is this in scope for v1.0?
- **Auth for the TUI:** the TUI exposes full trace content including reasoning. Should it require a passphrase or system auth before opening?
- **RBAC for audit records:** should different roles (developer, compliance officer, security) have different read/write access to trace data?
- **Encrypted SQLite:** SQLCipher integration for at-rest encryption without requiring Postgres?
