# recut-ai

When an AI agent fails in production, there is no replay. You see the final output — or the bill — but not why it went wrong, when it started drifting, or what would have happened if step 4 had returned a different result.

recut gives you that replay button. Wrap any agent with one decorator and get five primitives: **peek** inside a run, **intercept** it mid-flight, **replay** from any step with different inputs, **audit** the full trace, or **stress-test** it with auto-generated variants.

For anyone building with or responsible for agents — engineers, AI engineers, PMs, analysts, compliance teams. No ML background required.

---

## Install

```bash
pip install recut-ai
```

For framework-specific adapters (optional, install only what you need):

```bash
pip install recut-langchain    # LangChain + LangSmith enrichment
pip install recut-langgraph    # LangGraph native interrupt/replay
pip install recut-crewai       # CrewAI before/after hooks
pip install recut-otel         # Universal OpenTelemetry adapter (AutoGen, Semantic Kernel, Datadog, Phoenix)
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

---

## CLI

```
recut run "prompt"                     # run and trace, defaults to peek mode
recut peek   <trace-id>                # triage — surfaces high-risk steps only
recut audit  <trace-id>                # full structured audit pass
recut intercept "prompt"               # pause mid-run when a high-severity flag fires
recut replay <trace-id> --step 4       # fork from step 4, inject different context
recut diff   <trace-id> <fork-id>      # side-by-side behavioral diff
recut stress <trace-id> --variants 5   # stress-test with auto-generated variants
recut export <trace-id>                # export to .recut.json
```

---

## Flags

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

Every flag shows which layer fired it — `[rule]`, `[embedding]`, `[native]`, or `[judge]` — so you always know whether a signal is deterministic or model-generated.

---

## Flagging Engine

Detection runs in four layers, cheapest first:

1. **Rule-based** — free, instant, deterministic
2. **Embedding similarity** — cosine distance from original prompt (optional, `sentence-transformers`)
3. **Native thinking analysis** — Claude-only; reads extended thinking blocks directly
4. **LLM judge** — defaults to a local [Ollama](https://ollama.com) model (`llama3.2`); no data leaves your machine, no API cost. Bring your own API key (`RECUT_L4_BACKEND=anthropic`) if you want higher-accuracy judgment on ambiguous steps, with a configurable remote call limit (`RECUT_L4_REMOTE_MAX_PCT`, default 20%).

Use `flagging_depth="fast"` to run only layers 1–3 (zero model cost, instant). Use `"full"` to include layer 4.

---

## Framework Adapters

recut enriches your existing stack — it does not replace it. Each adapter pushes behavioral flags and reasoning signal into tools your team already uses.

**LangGraph** — recut's intercept mode maps directly onto LangGraph's `interrupt()` primitive. Pause on a high-severity flag, inspect the trace, then resume or redirect — all within the graph.

**LangChain + LangSmith** — implements `BaseCallbackHandler`, capturing reasoning tokens and posting behavioral flags as LangSmith feedback records. Open LangSmith and see your existing traces with a `recut_flags` column.

**CrewAI** — before/after hooks run synchronously and can block, making intercept mode available without LangGraph.

**OpenTelemetry** (`recut-otel`) — a `SpanProcessor` that enriches any OTel-instrumented stack. One adapter, works with AutoGen, Semantic Kernel, Arize Phoenix, Datadog, Honeycomb, Grafana.

**Langfuse** — posts CATEGORICAL and NUMERIC scores with a standardised `score_config` vocabulary. Turns Langfuse's scoring panel into a behavioral dashboard.

---

## Works with Claude and OpenAI

- **Claude**: native extended thinking block capture — real internal reasoning, not a summary
- **OpenAI**: inferred reasoning fallback

---

See [POSITIONING.md](docs/product/POSITIONING.md) for competitive landscape and use cases.  
See [ROADMAP.md](docs/product/ROADMAP.md) for what's coming.  
See [INTEGRATIONS.md](docs/product/INTEGRATIONS.md) for full adapter and platform integration detail.
