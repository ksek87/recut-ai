# recut-ai Roadmap

**Current:** `v0.1` in progress

---

## v0.1 — Foundation

- [ ] Scaffold repo structure and `pyproject.toml`
- [ ] Build Pydantic schema models (trace, fork, audit, stress, hooks)
- [ ] Set up SQLModel storage layer with SQLite
- [ ] Build `AbstractProvider` interface
- [ ] Async-first: all core interfaces must be async from the start

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

## v0.6 — Tests + Hardening

- [ ] Record trace fixtures for offline testing
- [ ] Test suite — schema, flagging, replay, tracer

---

## v1.0 — Stable Release

- [ ] Public API freeze
- [ ] Full docs + examples
- [ ] PyPI publish

## v1.5 — Polish

- [ ] Step deduplication across stress variants
- [ ] Lazy schema hydration for faster Peek mode startup
- [ ] Token budget awareness across providers
