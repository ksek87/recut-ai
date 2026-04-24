# recut-ai Roadmap

**Current:** `v0.1` in progress

> **Plan alignment:** v0.1–v0.5 map directly to Phases 1–5 in [RECUT_PLAN.md](RECUT_PLAN.md). v0.6–v0.8 are additions beyond the original build spec: integrations, production hardening, and enterprise features developed in response to market research. The build order within each version follows the phase notes in RECUT_PLAN.md.

---

## v0.1 — Foundation

- [ ] Scaffold repo structure and `pyproject.toml`
- [ ] Build Pydantic schema models (trace, fork, audit, stress, hooks)
- [ ] Set up SQLModel storage layer with SQLite
- [ ] Build `AbstractProvider` interface
- [ ] Async-first: all core interfaces must be async from the start
- [ ] Non-blocking guarantee: recut failures must never surface to the agent caller
- [ ] Circuit breaker: auto-disable on repeated storage/flagging failures

## v0.2 — Core Capture

- [ ] Build Anthropic provider — native thinking block capture
- [ ] Build `@recut.trace` decorator — wraps any function, captures steps
- [ ] Build flagging engine — layered (rules → embeddings → native mismatch → batched LLM)
- [ ] Build plain language summariser
- [ ] Streaming trace capture + flag result caching

## v0.3 — Modes

- [ ] Peek mode — fast triage, surfaces high-risk steps only
- [ ] Audit mode — full structured pass, `AuditRecord` output
- [ ] Replay mode — fork at step, inject, run forward, diff
- [ ] Intercept mode — pause mid-run, inspect, redirect
- [ ] Stress mode — auto-generate variants from flagged steps
- [ ] Selective tracing (`sample_rate`, `trace_if`)
- [ ] `flagging_depth: "fast" | "full"` — fast = layers 1-3 only (zero meta-LLM cost), full = all 4 layers; defaults to fast
- [ ] Per-layer flag attribution — every flag shows which layer fired it (`[rule]`, `[embedding]`, `[native]`, `[judge]`) in peek and audit output
- [ ] Token cost attribution — `token_cost_usd` per step and per trace; surfaced in peek output and TUI dashboard
- [ ] Structured LLM judge output — layer 4 returns per-flag `confidence` (0-1) and `evidence` (quoted step text) alongside score; no free-text black-box verdicts

### Layer 4 — Bring Your Own Model (Ollama-first)

Layer 4 is the LLM judge. It should never require an API key or send data to a third party by default.

**Default: Ollama (local, offline, free)**
- `RECUT_L4_BACKEND=ollama` (default)
- `RECUT_L4_OLLAMA_URL=http://localhost:11434` (standard Ollama address)
- `RECUT_L4_OLLAMA_MODEL=llama3.2` (default model; any Ollama-compatible model works)
- If Ollama is not running, Layer 4 is silently skipped — no error, no cost
- Zero data exfiltration, works fully offline, no API key, no billing surprises

**Remote API (BYOK — bring your own key):**
- `RECUT_L4_BACKEND=anthropic|openai`
- Uses existing `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from env
- `RECUT_META_MODEL` selects the model (default `claude-haiku-4-5-20251001`)

**Remote call limit (configurable, default 20%):**
- `RECUT_L4_REMOTE_MAX_PCT=0.20` — at most 20% of total steps in a trace may escalate to the remote API Layer 4 judge; the rest are handled by the local Ollama model or skipped
- Applies only when `RECUT_L4_BACKEND` is a remote API — Ollama is uncapped (local, no cost)
- Also supports `RECUT_L4_MAX_REMOTE_CALLS=N` (hard per-trace integer cap; whichever limit is hit first applies)
- Sampling within the cap is weighted toward tool_call and output steps — the highest-stakes step types
- Rationale: if layers 1-3 are doing their job, Layer 4 should only be touching a small fraction of steps; the 20% cap enforces that and keeps remote API costs bounded and predictable

**Marketing framing:** Layer 4 uses a local model by default. No data leaves your machine. If you want higher-accuracy judgment on ambiguous steps and are comfortable with API costs, bring your own key and set your own limits.

## v0.4 — CLI + TUI

- [ ] Typer CLI commands: `run`, `intercept`, `replay`, `diff`, `peek`, `audit`, `stress`, `export`
- [ ] Textual TUI — peek queue view
- [ ] Textual TUI — audit walkthrough view
- [ ] Textual TUI — side-by-side diff view

## v0.5 — Export + Hooks

- [ ] `.recut.json` exporter
- [ ] `@recut.on_flag` hook system
- [ ] OpenAI provider — inferred reasoning fallback

## v0.6 — Integrations (SDK-First, Adapter Architecture)

recut enriches existing tools — it does not replace them. The integration model has two layers:

**Layer A — Framework adapters (embed recut into agent execution)**
These ship as separate namespace packages so users only install what they need.

| Package | Framework | Integration point | Intercept capable? |
|---|---|---|---|
| `recut-otel` | Any OTel-instrumented (AutoGen, Semantic Kernel) | `SpanProcessor` injected into `TracerProvider` | No (observational) |
| `recut-langgraph` | LangGraph | `interrupt_before/after` + graph state + checkpointer | **Yes — native** |
| `recut-langchain` | LangChain / LangSmith | `BaseCallbackHandler` on `on_llm_end`, `on_agent_action`, `on_custom_event` | No (read-only) |
| `recut-langfuse` | Any + Langfuse | Langfuse `span.score()` CATEGORICAL/NUMERIC post-run | No |
| `recut-crewai` | CrewAI | `before_llm_call_hook` + `after_tool_call_hook` (synchronous, can block) | **Yes — hooks block** |
| `recut-llamaindex` | LlamaIndex | `CallbackManager` event registration | No (read-only) |

**Build order (by impact / user base):**
1. `recut-otel` first — one adapter unlocks AutoGen, Phoenix, Datadog, Honeycomb, Grafana simultaneously; no framework-specific knowledge required from users
2. `recut-langgraph` second — strongest intercept integration; LangGraph's `interrupt()` is exactly recut's intercept model
3. `recut-langchain` third — largest installed base; `BaseCallbackHandler` + LangSmith `create_feedback()` covers every LangChain/LangSmith user
4. `recut-langfuse` fourth — Langfuse scoring API (`CATEGORICAL` scores with `score_config`) is the most capable enrichment surface of any platform
5. `recut-crewai` fifth — growing fast; hooks can block, making intercept mode available without LangGraph

**Layer B — Observability platform enrichment (push recut signal into existing dashboards)**
- [ ] OpenTelemetry exporter — `recut.*` span attributes + GenAI semantic conventions; unlocks Datadog, Phoenix, Honeycomb, Grafana
- [ ] LangSmith enrichment — reasoning content + behavioral flags as `create_feedback()` records; `source_info={"source": "recut"}` for audit provenance
- [ ] Langfuse scoring — `CATEGORICAL` flag types + `NUMERIC` confidence scores via `span.score()`; `score_config` creates a standardised recut vocabulary inside Langfuse
- [ ] Fiddler AI adapter — behavioral flags + risk scores as custom event columns via `fiddler-client`
- [ ] W&B Weave adapter — risk metrics + stress variant comparison tables via `recut.*` span attributes
- [ ] Slack alerter — `on_flag` hook, high-severity flag notifications
- [ ] PagerDuty alerter — production on-call integration with dedup and severity routing
- [ ] Generic webhook exporter — HTTP POST for internal systems

**TypeScript / JavaScript:**
- [ ] `recut-js` — OTel span processor + LangGraph.js interrupt integration; covers Vercel AI SDK, LangChain.js, Mastra, and any fetch-based Anthropic/OpenAI client

See [INTEGRATIONS.md](INTEGRATIONS.md) for full design detail.

## v0.7 — Production Hardening

See [ENTERPRISE.md](ENTERPRISE.md) for full detail.

- [ ] PagerDuty alerter — production on-call integration with dedup
- [ ] PII & secret scrubber — runs in-process before any write or export
- [ ] Reasoning block sensitivity controls (`store_native`, `export_native`, `truncate_native_at`)
- [ ] Trace integrity sealing — SHA-256 content hash, tamper-evident audit records
- [ ] Data residency controls — `RECUT_INTEGRATION_ALLOWLIST`, `RECUT_EXPORT_ALLOWED`
- [ ] PostgreSQL storage backend — for multi-process / high-volume deployments
- [ ] Async write queue with backpressure and graceful drop
- [ ] Trace size limits (`RECUT_MAX_STEPS_PER_TRACE`, `RECUT_MAX_CONTENT_LENGTH`)
- [ ] Retention & auto-cleanup (`RECUT_TRACE_TTL_DAYS`, `recut db vacuum`)
- [ ] CLI audit log (`~/.recut/audit.log`) — timestamp, user, command, trace ID
- [ ] Compliance export format (`recut export --format compliance`)
- [ ] **Behavioral fingerprinting** — per-agent baseline profiles built from local trace history (SQLite); new runs scored by Z-score deviation ("3.1σ more tool calls than baseline"); fully local, no model, no API; surfaces as `[fingerprint]` flag source in peek output
- [ ] **`recut calibrate`** — reads human audit review outcomes (`AuditRecord.review_status`) from local store, adjusts per-flag-type decision thresholds; fingerprinting and flagging improve from your own production data over time
- [ ] **Per-agent sampling overrides** — `sample_rate` per agent_id, not just globally; severity-weighted sampling (high-risk agents always at 100%, low-risk at configurable rate)
- [ ] **Hard budget kill-switch** — `budget_hard_limit=True` on `@recut.trace()` raises `RecutBudgetExceededError` and fires on_flag hook when token budget is exceeded, not just a warning log

## v0.8 — Tests + Hardening

- [ ] Record trace fixtures for offline testing
- [ ] Test suite — schema, flagging, replay, tracer

## v1.0 — Stable Release

- [ ] Public API freeze
- [ ] Full docs + examples
- [ ] PyPI publish

## v1.5 — Polish

- [ ] Step deduplication across stress variants
- [ ] Lazy schema hydration for faster Peek mode startup
- [ ] Token budget awareness with live TUI spend display
- [ ] SQLCipher integration for encrypted SQLite (zero-config at-rest encryption)
- [ ] RBAC for audit records (developer / compliance officer / security roles)
- [ ] **Local ONNX classifier for layer 4** — once recut has a curated corpus of flagged traces, replace the meta-LLM judge with a small local model (zero API cost, zero latency, zero data sharing); deferred until training data exists
- [ ] **Calibration report** — `recut calibrate --report` shows layer 4 accuracy against human-reviewed audit records: how many judge flags were confirmed, how many were false positives; makes the LLM judgment feel like an instrument reading, not an oracle
