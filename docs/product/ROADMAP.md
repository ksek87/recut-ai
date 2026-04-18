# recut-ai Roadmap

**Current:** `v0.1` in progress

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
- [ ] Slack alerter — `on_flag` hook, high-severity flag notifications
- [ ] Generic webhook exporter — HTTP push for internal systems
- [ ] W&B Weave adapter — risk metrics + stress variant comparison tables
- [ ] PagerDuty alerter — production on-call integration with dedup

## v0.7 — Production Hardening

See [ENTERPRISE.md](ENTERPRISE.md) for full detail.

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
