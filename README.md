# recut — AI Agent Observability, Debugging & Replay for Python

**Production-grade observability for AI agents.** One decorator gives you mid-flight interception, counterfactual replay, behavioral flagging, and full audit trails — for Claude, OpenAI, LangChain, LangGraph, and CrewAI agents.

```python
pip install recut-ai
```

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Why recut?

When an AI agent fails in production, there is no replay. You see the final output — or the bill — but not **why** it went wrong, **when** it started drifting, or what would have happened if step 4 had returned a different result.

recut gives you that replay button. Wrap any async agent with one decorator and get five primitives:

| Primitive | What it does |
|---|---|
| `peek` | Triage a completed trace — surfaces high-risk steps only |
| `intercept` | Pause a live agent mid-run when a behavioral flag fires |
| `replay` | Fork from any step and inject different context |
| `audit` | Full structured audit with four flagging layers + LLM judge |
| `stress` | Auto-generate adversarial variants from flagged steps |

Built for engineers, AI engineers, PMs, and compliance teams. No ML background required.

---

## Install

```bash
pip install recut-ai
```

Framework adapters (optional):

```bash
pip install recut-langchain    # LangChain + LangSmith enrichment
pip install recut-langgraph    # LangGraph native interrupt/replay
pip install recut-crewai       # CrewAI before/after hooks
pip install recut-otel         # OpenTelemetry adapter (AutoGen, Semantic Kernel, Datadog, Phoenix)
pip install recut-langfuse     # Langfuse behavioral scoring
```

```bash
cp .env.example .env  # add ANTHROPIC_API_KEY or OPENAI_API_KEY
```

---

## Quick Start

```python
import recut

@recut.trace(agent_id="my-agent", mode="peek")
async def run_agent(prompt: str, ctx=None) -> str:
    async for step in ctx.provider.run_agent(prompt):
        ctx.add_step(step)
    return ctx.trace.steps[-1].content

@recut.on_flag
def handle_flag(event: recut.RecutFlagEvent):
    print(f"[{event.flag.severity}] {event.flag.plain_reason}")
```

### Budget guardrails

Stop an agent that's burning tokens:

```python
@recut.trace(
    agent_id="my-agent",
    token_budget=0.10,        # hard stop at $0.10
    budget_hard_limit=True,
)
async def run_agent(prompt: str, ctx=None) -> str:
    ...
```

### Inline flagging

Score every run immediately without a background job:

```python
@recut.trace(agent_id="my-agent", flagging_depth="fast")   # layers 1-3, instant
@recut.trace(agent_id="my-agent", flagging_depth="full")   # all layers + LLM judge
```

---

## CLI

```
recut run "prompt"                     # run and trace in peek mode
recut peek   <trace-id>                # triage — surfaces high-risk steps only
recut audit  <trace-id>                # full structured audit pass
recut intercept "prompt"               # pause mid-run when a high-severity flag fires
recut replay <trace-id> --step 4       # fork from step 4, inject different context
recut diff   <trace-id> <fork-id>      # side-by-side behavioral diff
recut stress <trace-id> --variants 5   # stress-test with auto-generated adversarial variants
recut export <trace-id>                # export trace to .recut.json
```

---

## Behavioral Flags

Every flag ships with a plain-language reason — readable by anyone on the team, not just engineers.

| Flag | What it means |
|---|---|
| `overconfidence` | Agent states certainty it doesn't have |
| `goal_drift` | Agent has moved away from the original task |
| `scope_creep` | Agent is doing significantly more than asked |
| `reasoning_gap` | Agent acts without adequate reasoning |
| `uncertainty_suppression` | Agent hides or downplays genuine uncertainty |
| `instruction_deviation` | Agent contradicts or ignores the original instructions |
| `anomalous_tool_use` | Tool use is unexpected, repeated, or unjustified |
| `reasoning_action_mismatch` | *(Claude only)* Private reasoning expresses doubt; action expresses confidence |

Every flag shows which detection layer fired it — `[rule]`, `[embedding]`, `[native]`, `[judge]`, or `[fingerprint]` — so you always know whether a signal is deterministic or model-generated.

---

## Flagging Engine — Five Detection Layers

Detection runs cheapest-first, so fast traces stay fast:

1. **Rule-based** — deterministic, instant, zero cost. Catches scope creep, tool abuse, and deviation from instructions without any model call.
2. **Embedding similarity** — cosine distance from the original prompt catches goal drift. Uses `sentence-transformers` locally; no API call.
3. **Native thinking analysis** — Claude-exclusive. Reads extended thinking blocks directly; detects reasoning-action mismatches that no other tool can see.
4. **LLM judge** — defaults to a local model via any OpenAI-compatible runtime (Ollama, LM Studio, llama.cpp, vLLM). **No data leaves your machine, no API cost.** Set `RECUT_L4_BACKEND=anthropic|openai` for cloud judgment on ambiguous steps.
5. **Behavioral fingerprinting** — after ~5 traces, builds a per-agent baseline from SQLite history and flags statistical deviations (step count, risk score) as `[fingerprint]`. Pure statistics, zero API calls.

Use `flagging_depth="fast"` for layers 1–3 only (instant, free). Use `"full"` to include the LLM judge.

---

## Configuration

All behavior is configurable via environment variables — no config file needed:

```bash
RECUT_L4_BACKEND=local               # local (default, free) | anthropic | openai
RECUT_L4_LOCAL_URL=http://localhost:11434/v1   # Ollama, LM Studio, vLLM, etc.
RECUT_META_MODEL_ANTHROPIC=claude-haiku-4-5-20251001  # per-backend model override
RECUT_DEFAULT_SAMPLE_RATE=0.1        # trace 10% of production calls
RECUT_SCOPE_CREEP_THRESHOLD=20       # flag after this many steps
RECUT_STRESS_VARIANTS=3              # variants per stress run
RECUT_PII_SCRUB=true                 # scrub email, phone, SSN before storage
RECUT_PRICE_INPUT=3.0                # input token price per million
RECUT_PRICE_OUTPUT=15.0              # output token price per million
RECUT_CACHE_ENABLED=true             # cache flag results across identical steps
```

See **[docs/configuration.md](docs/configuration.md)** for the full reference — 40+ env vars covering Layer 4 tuning, embedding settings, flagging thresholds, risk weights, stress testing, replay, fingerprinting, PII scrubbing, caching, sampling, and storage.

---

## Framework Integrations

recut enriches your existing observability stack — it does not replace it.

**LangGraph** — recut's `intercept` mode maps directly onto LangGraph's `interrupt()` primitive. Pause on a high-severity flag, inspect the trace, then resume or redirect — all within the graph.

**LangChain + LangSmith** — implements `BaseCallbackHandler`, capturing reasoning tokens and posting behavioral flags as LangSmith feedback records. See a `recut_flags` column in your existing LangSmith traces.

**CrewAI** — synchronous before/after hooks that can block, making intercept mode available without LangGraph.

**OpenTelemetry** (`recut-otel`) — a `SpanProcessor` that enriches any OTel-instrumented agent. One adapter works with AutoGen, Semantic Kernel, Arize Phoenix, Datadog, Honeycomb, and Grafana.

**Langfuse** — posts `CATEGORICAL` and `NUMERIC` scores with a standardized `score_config` vocabulary. Turns Langfuse's scoring panel into a behavioral dashboard.

---

## Claude & OpenAI Support

- **Claude** — native extended thinking block capture. recut reads the actual internal reasoning Claude produces, not a summary. This is the only tool that can detect reasoning-action mismatches at the model level.
- **OpenAI** — inferred reasoning fallback; all other flagging layers apply.

---

## Storage & Privacy

Traces are stored locally in SQLite by default (zero external dependencies). PostgreSQL is supported for multi-process or hosted deployments. PII scrubbing (`RECUT_PII_SCRUB=true`) applies regex redaction to prompts and step content before storage — the in-memory trace is never mutated.

---

See [POSITIONING.md](docs/product/POSITIONING.md) for competitive landscape and use cases.  
See [ROADMAP.md](docs/product/ROADMAP.md) for what's coming.  
See [INTEGRATIONS.md](docs/product/INTEGRATIONS.md) for full adapter and platform integration detail.
