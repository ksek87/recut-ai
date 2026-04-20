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

## v0.4 — CLI + TUI

- [ ] Typer CLI commands: `run`, `intercept`, `replay`, `diff`, `peek`, `audit`, `stress`, `export`
- [ ] Textual TUI — peek queue view
- [ ] Textual TUI — audit walkthrough view
- [ ] Textual TUI — side-by-side diff view

## v0.5 — Export + Hooks

- [ ] `.recut.json` exporter
- [ ] `@recut.on_flag` hook system
- [ ] OpenAI provider — inferred reasoning fallback

## v0.6 — Integrations

Recut enriches existing tools — it does not replace them. See [INTEGRATIONS.md](INTEGRATIONS.md) (same dir).

- [ ] OpenTelemetry exporter — spans + flag events, unlocks Datadog, Phoenix, Honeycomb, Grafana
- [ ] LangSmith adapter — reasoning content + flag scores as LangSmith feedback
- [ ] Langfuse adapter — behavioral scores + plain-language reasons via scoring API
- [ ] Fiddler AI adapter — behavioral flags + risk scores as custom event columns via `fiddler-client`
- [ ] W&B Weave adapter — risk metrics + stress variant comparison tables
- [ ] Slack alerter — `on_flag` hook, high-severity flag notifications
- [ ] Generic webhook exporter — HTTP push for internal systems

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
