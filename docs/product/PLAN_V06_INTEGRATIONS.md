# v0.6 — Integrations Plan

## Goal

Recut enriches existing observability tools; it doesn't replace them. Each integration is an `on_flag` hook or an async exporter that pushes recut data into a third-party system. No new core abstractions are needed — all integrations live in `recut/integrations/`.

---

## File layout

```
recut/integrations/
├── __init__.py
├── otel.py          # OpenTelemetry — spans + flag events
├── langsmith.py     # LangSmith — reasoning content + flag scores as feedback
├── langfuse.py      # Langfuse — behavioral scores via scoring API
├── slack.py         # Slack — on_flag hook, high-severity alerts
├── webhook.py       # Generic HTTP webhook push
└── wandb.py         # W&B Weave — risk metrics + stress variant tables
```

Each module exports a single setup function (`setup_*`) and/or an `on_flag` compatible handler. Users opt in explicitly — no auto-import.

---

## 1. OpenTelemetry (`otel.py`) — ship first

**Why first:** OTel unlocks Datadog, Grafana, Honeycomb, Phoenix, and Arize for free. One integration, many destinations.

**Design:**
- Depends on: `opentelemetry-api`, `opentelemetry-sdk` (optional dep group `otel`)
- `setup_otel(service_name, exporter)` — configures a `TracerProvider` and stores it module-level
- `recut_span(trace: RecutTrace) -> None` — creates a root OTel span per recut trace, with child spans per step. Attach flag events as span events with attributes: `recut.flag.type`, `recut.flag.severity`, `recut.flag.source`
- `on_flag_otel(event: RecutFlagEvent) -> None` — standalone `on_flag` hook that emits a span event for live interception

**Key span attributes:**
```
recut.trace.id          string
recut.agent.id          string
recut.model             string
recut.step.index        int
recut.step.type         string
recut.flag.type         string
recut.flag.severity     string  (low | medium | high)
recut.flag.source       string  (rule | embedding | native | llm)
recut.risk.score        float
```

**Usage:**
```python
from recut.integrations.otel import setup_otel, recut_span
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

setup_otel("my-agent-service", OTLPSpanExporter(endpoint="http://localhost:4317"))

# After audit:
recut_span(trace)
```

---

## 2. LangSmith (`langsmith.py`)

**Design:**
- Depends on: `langsmith` (optional dep group `langsmith`)
- `LangSmithHook(client, project_name)` — `on_flag` handler class
- On flag: calls `client.create_feedback(run_id=..., key=flag.type.value, score=severity_to_score(flag.severity), comment=flag.plain_reason)`
- Reasoning content posted as `metadata` on the LangSmith run

**Severity → score mapping:** `low=0.3`, `medium=0.6`, `high=1.0`

**Note:** Requires the agent run to be instrumented with LangSmith run IDs. The `RecutFlagEvent` will need a `run_id: str | None` field added to `schema/hooks.py`.

---

## 3. Langfuse (`langfuse.py`)

**Design:**
- Depends on: `langfuse` (optional dep group `langfuse`)
- `LangfuseScorer(client)` — takes a completed `AuditRecord` and posts scores via `client.score()`
- One score per flag type in the risk profile, value = count normalised to `[0, 1]` (capped at 5 flags = 1.0)
- Plain-language audit summary posted as a `generation` comment

---

## 4. Slack (`slack.py`)

**Design:**
- Depends on: `slack-sdk` (optional dep group `slack`)
- `SlackAlerter(token, channel, min_severity="high")` — `on_flag` handler class
- Posts a formatted Block Kit message with: agent ID, step index, flag type, severity, plain reason, suggested action
- Rate-limited: deduplicate identical flag+step combos within a 5-minute window (in-process `dict` keyed by `(trace_id, step_id, flag_type)`)

**Message format:**
```
🚨 [HIGH] recut flag — my-agent
Flag:    anomalous_tool_use
Step:    7
Reason:  The agent called the same tool with identical inputs more than once — this looks like a loop.
Action:  replay
```

---

## 5. Generic webhook (`webhook.py`)

**Design:**
- Depends on: `httpx` (already a dep)
- `WebhookAlerter(url, secret=None, min_severity="medium")` — `on_flag` handler
- `POST` JSON payload: full `RecutFlagEvent.model_dump()` to `url`
- If `secret` is set, adds `X-Recut-Signature: sha256=<hmac>` header (HMAC-SHA256 of the body with the secret)
- Non-blocking: fires in a background task, never raises to caller

**Security note:** The HMAC signature allows webhook receivers to verify the payload is from a trusted recut instance. This is the same pattern used by GitHub and Stripe webhooks.

---

## 6. W&B Weave (`wandb.py`)

**Design:**
- Depends on: `wandb` (optional dep group `wandb`)
- `log_stress_run(stress_runs: list[RecutStressRun], project)` — logs a W&B table with columns: strategy, risk_delta, verdict, plain_summary
- `log_trace_metrics(trace, record)` — logs scalar metrics: flag_count, risk_score, duration_seconds

---

## pyproject.toml additions

```toml
[project.optional-dependencies]
otel = ["opentelemetry-api>=1.20", "opentelemetry-sdk>=1.20"]
langsmith = ["langsmith>=0.1"]
langfuse = ["langfuse>=2.0"]
slack = ["slack-sdk>=3.20"]
wandb = ["wandb>=0.16"]
integrations = [
    "recut-ai[otel,langsmith,langfuse,slack,wandb]"
]
```

---

## Build order

1. `webhook.py` — no new deps, validates the pattern, ships the HMAC security model
2. `otel.py` — highest leverage, unlocks 5+ platforms
3. `slack.py` — fastest path to human-in-the-loop for enterprise
4. `langsmith.py` / `langfuse.py` — ML-first users
5. `wandb.py` — red team / stress testing workflows

---

## Schema change needed

Add `run_id: str | None = None` to `RecutFlagEvent` in `recut/schema/hooks.py` to support LangSmith and Langfuse run correlation.
