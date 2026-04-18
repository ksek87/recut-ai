# recut-ai — Integration Strategy

**Philosophy: recut enriches your existing stack, it doesn't replace it.**

Your existing tools are good at what they do. LangSmith tracks runs. Langfuse stores traces. Helicone logs requests. W&B Weave records experiments. None of them capture reasoning, replay forks, or intercept mid-run — that's what recut adds. The goal is to make those tools *more useful*, not to compete with them.

---

## The Integration Model

recut has two natural integration points:

**1. The hook system (`@recut.on_flag`)**
Fires when a step is flagged. Use it to push flag events — with full context — into any external system. Slack, PagerDuty, LangSmith, a custom webhook, anything.

**2. The exporter (`recut export`)**
Produces a structured `.recut.json` after a run. Adapters can translate this into the native format of any observability platform.

Every integration in this doc is built on one or both of these two primitives. No monkey-patching, no proxy layer, no changes to your agent code.

```
Your Agent
    ↓
@recut.trace                          ← one decorator
    ├── captures steps + reasoning
    ├── runs flagging engine
    ├── fires on_flag hooks  ──────────→ LangSmith / Langfuse / Slack / PagerDuty
    └── stores trace locally
         └── recut export  ───────────→ .recut.json → any platform
```

---

## Foundation: OpenTelemetry

The right foundation for all integrations is OpenTelemetry (OTel). It's the vendor-neutral standard — if recut emits OTel spans, it automatically works with Datadog, Honeycomb, Grafana Tempo, Jaeger, Arize Phoenix, and anything else that's OTel-compatible.

**What recut emits as OTel:**

| recut concept | OTel mapping |
|---------------|-------------|
| `RecutTrace` | Root span (`recut.trace`) |
| `RecutStep` | Child span (`recut.step`) |
| `StepReasoning` | Span attribute (`recut.reasoning.content`, `recut.reasoning.source`) |
| `RecutFlag` | Span event (`recut.flag`) with attributes: type, severity, plain_reason, source |
| `risk_score` | Span attribute (`recut.risk_score`) |
| Fork / replay | Linked span with `recut.fork.parent_trace_id` |

**Usage:**

```python
from recut.integrations.otel import OtelExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

recut.configure(
    exporters=[
        OtelExporter(
            exporter=OTLPSpanExporter(endpoint="http://localhost:4317")
        )
    ]
)
```

After this, every recut trace appears in your existing OTel backend — Phoenix, Honeycomb, Grafana — enriched with reasoning content and behavioral flags that your backend has never seen before.

---

## LangSmith

LangSmith is a tracing + evaluation platform for LangChain users. It stores runs in a tree structure. recut sends a structured run record with flag events attached as feedback scores.

**What recut adds to LangSmith:**
- Reasoning block content (LangSmith shows inputs/outputs, not thinking)
- Flag scores as LangSmith feedback (overconfidence: 0.85, goal_drift: 0.2, etc.)
- Step-level risk scores attached to child runs
- Replay forks as linked sibling runs

**Setup:**

```python
from recut.integrations.langsmith import LangSmithExporter

recut.configure(
    exporters=[LangSmithExporter(project_name="my-agent")]
)
```

Reads `LANGCHAIN_API_KEY` from env. No other config needed.

**What it posts:**
- A top-level run for each `RecutTrace`
- Child runs for each `RecutStep`
- LangSmith feedback entries for each `RecutFlag` (score = severity mapped to 0–1)
- Tag `recut:reasoning_source=native` for Claude thinking blocks

**Result:** Open LangSmith, see your existing traces, now with a `recut_flags` feedback column and reasoning content in step outputs. Filter runs by `recut:high_severity` to find the bad ones.

---

## Langfuse

Langfuse is open-source LLM observability. It uses a `trace → span → generation` model and supports custom scores. recut maps cleanly onto it.

**What recut adds to Langfuse:**
- Reasoning content as generation metadata
- Flag types and severities as Langfuse scores
- Plain-language flag reasons in span output
- Replay forks as separate traces linked by `parent_trace_id`

**Setup:**

```python
from recut.integrations.langfuse import LangfuseExporter

recut.configure(
    exporters=[LangfuseExporter()]
)
```

Reads `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` from env.

**What it posts:**
- `langfuse.trace()` per `RecutTrace`
- `langfuse.span()` per `RecutStep`
- `langfuse.generation()` for each LLM call step, with `reasoning` in metadata
- `langfuse.score()` per `RecutFlag` — name = flag type, value = severity float, comment = plain_reason

**Result:** Langfuse shows your traces with recut scores in the scoring panel. You can filter and sort by `overconfidence`, `goal_drift`, etc. — turning Langfuse's existing scoring UI into a behavioral dashboard.

---

## Weights & Biases Weave

W&B Weave is built for experiment tracking and LLM call logging. It tracks `op` calls and lets you attach custom attributes.

**What recut adds to Weave:**
- Reasoning blocks as call attributes
- Flag events as custom feedback
- Risk scores as metrics per run
- Stress test results as a W&B Table (variant × verdict × risk_delta)

**Setup:**

```python
from recut.integrations.wandb import WeaveExporter

recut.configure(
    exporters=[WeaveExporter(project="my-agent-project")]
)
```

**What it posts:**
- `weave.op` call per trace with recut metadata
- Custom attributes: `recut.flags`, `recut.risk_score`, `recut.reasoning_source`
- For stress runs: a `wandb.Table` with one row per variant (strategy, verdict, risk_delta, plain_summary)

**Result:** W&B Weave shows agent runs alongside your existing experiment data. The stress table is particularly useful here — you can compare stress variants across agent versions as you iterate.

---

## Slack

Real-time alerting when a high-severity flag fires mid-run.

**Setup:**

```python
from recut.integrations.slack import SlackAlerter

@recut.on_flag
def alert(event: recut.RecutFlagEvent):
    if event.flag.severity == "high":
        SlackAlerter(webhook_url=os.environ["SLACK_WEBHOOK_URL"]).send(event)
```

**Message format:**
```
⚠️ recut flag — HIGH severity
Agent:   my-agent
Flag:    reasoning_action_mismatch
Reason:  "The agent seemed unsure in its thinking but acted confidently anyway."
Step:    4 / tool_call → search_database
Trace:   abc123  →  recut peek abc123
```

---

## PagerDuty

For production agents where a high-severity flag should page someone.

```python
from recut.integrations.pagerduty import PagerDutyAlerter

@recut.on_flag
def page(event: recut.RecutFlagEvent):
    if event.flag.severity == "high" and event.flag.type == "anomalous_tool_use":
        PagerDutyAlerter(
            routing_key=os.environ["PAGERDUTY_ROUTING_KEY"]
        ).trigger(event)
```

Uses PagerDuty Events API v2. Dedup key = `trace_id:step_id` so repeat flags on the same step don't spam.

---

## Generic Webhook

For any system not listed above:

```python
from recut.integrations.webhook import WebhookExporter

recut.configure(
    exporters=[
        WebhookExporter(
            url="https://your-system.internal/recut-events",
            headers={"Authorization": "Bearer ..."},
            events=["on_flag", "on_trace_complete"]  # subscribe to specific events
        )
    ]
)
```

Payload is the serialized `RecutFlagEvent` or `RecutTrace` as JSON. Retries on 5xx with exponential backoff.

---

## Integration Priority & Build Order

| Integration | Why | Phase |
|-------------|-----|-------|
| OpenTelemetry | Universal foundation — unlocks Datadog, Phoenix, Honeycomb, Grafana for free | v0.5 |
| LangSmith | Largest existing user base for LLM tracing | v0.5 |
| Langfuse | Open-source, growing fast, scores API is a perfect fit | v0.5 |
| Slack | Fastest path to real-time alerting for any team | v0.5 |
| W&B Weave | Strong fit for stress mode / experiment tracking | v0.6 |
| PagerDuty | Enterprise / on-call workflows | v0.6 |
| Generic Webhook | Long tail of internal systems | v0.5 |

---

## What recut contributes to each tool

| Tool | recut adds |
|------|-----------|
| LangSmith | Reasoning content in step outputs + flag scores as feedback |
| Langfuse | Behavioral scores (flag types) + plain-language reasons |
| W&B Weave | Risk metrics per run + stress variant comparison tables |
| Arize Phoenix | Native OTel spans with reasoning attributes + flag events |
| Datadog / Honeycomb | OTel spans enriched with behavioral signals |
| Slack / PagerDuty | Real-time high-severity flag alerts with plain-language context |

---

## What this means for the README

The integration story is a first-class feature, not a footnote. The README should lead with:

> Already using LangSmith? Langfuse? recut sends reasoning data and behavioral flags into your existing traces. Nothing to replace.

---

## Open Questions for v1.0

- **Should recut support reading from LangSmith/Langfuse traces** (not just writing)? This would let you run audit/replay on traces you captured before adding recut.
- **OTel baggage propagation** — should recut propagate trace context so it's linked to your existing distributed traces (e.g. if the agent is one hop in a larger request)?
- **LangChain / LlamaIndex native wrappers** — should recut provide drop-in callbacks for these frameworks so the decorator isn't needed?
