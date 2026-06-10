# recut — AI Agent Observability, Debugging & Replay for Python

**Production-grade observability for AI agents.** Stop runaway costs, catch behavioral failures before they escalate, and replay any trace from the exact step it went wrong.

```bash
pip install recut-ai
```

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## The problem

AI agents fail silently and expensively. One Claude Code sub-agent burned 27M tokens in a single run. Another executed `DROP DATABASE` after a sequence of individually-reasonable decisions. A third looped on the same tool call 50,000 times before anyone noticed.

The common thread: no structured trace, no replay, and nothing to intercept the run before it does something irreversible.

---

## Quick start — zero-change instrumentation

Add one line. recut patches the Anthropic and OpenAI SDKs automatically — **no changes to your agent code**:

```python
import recut
recut.init(agent_id="my-service")   # patches anthropic + openai SDKs

# Your existing agent code runs completely unchanged:
client = anthropic.AsyncAnthropic()
response = await client.messages.create(
    model="claude-opus-4-8",
    messages=[{"role": "user", "content": prompt}],
)
```

Every call is now captured as a recut trace. View it in the CLI:

```bash
recut peek <trace-id>     # triage — surfaces high-risk steps only
recut audit <trace-id>    # full structured audit
```

---

## Or use the decorator for full control

```python
import recut

@recut.trace(
    agent_id="my-agent",
    token_budget=0.10,       # hard stop at $0.10
    budget_hard_limit=True,  # raises RecutBudgetExceededError if exceeded
    flagging_depth="fast",   # layers 1-3, instant, zero model cost
)
async def run_agent(prompt: str, ctx=None) -> str:
    async for step in ctx.provider.run_agent(prompt):
        ctx.add_step(step)
    return ctx.trace.steps[-1].content

@recut.on_flag
def handle_flag(event: recut.RecutFlagEvent):
    print(f"[{event.flag.severity}] {event.flag.plain_reason}")
```

---

## Install

```bash
pip install recut-ai              # core
pip install "recut-ai[embeddings]"  # + goal-drift detection via sentence-transformers
pip install "recut-ai[tui]"         # + interactive TUI (requires textual)
```

```bash
cp .env.example .env   # add ANTHROPIC_API_KEY or OPENAI_API_KEY
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

## Primitives

| Primitive | What it does |
|---|---|
| `peek` | Fast triage — surfaces only the highest-risk steps |
| `intercept` | Pause a live agent mid-run the moment a behavioral flag fires |
| `replay` | Fork from any step, inject different context, run forward |
| `audit` | Full structured audit with four flagging layers + LLM judge |
| `stress` | Auto-generate adversarial variants from flagged steps |

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

Detection runs cheapest-first so fast traces stay fast:

1. **Rule-based** — deterministic, instant, zero cost. Catches scope creep, tool loops, and instruction deviation without any model call.
2. **Embedding similarity** — cosine distance from the original prompt catches goal drift. Uses `sentence-transformers` locally; no API call.
3. **Native thinking analysis** — Claude-exclusive. Reads extended thinking blocks directly; detects reasoning-action mismatches that no other tool can see.
4. **LLM judge** — defaults to a local model via any OpenAI-compatible runtime (Ollama, LM Studio, llama.cpp, vLLM). **No data leaves your machine, no API cost.** Set `RECUT_L4_BACKEND=anthropic|openai` for cloud judgment.
5. **Behavioral fingerprinting** — after ~5 traces, builds a per-agent baseline from SQLite history and flags statistical deviations (step count, risk score) as `[fingerprint]`. Pure math, zero API calls.

Use `flagging_depth="fast"` for layers 1–3 only (instant, free). Use `"full"` to include the LLM judge.

---

## Configuration

All behavior is configurable via environment variables:

```bash
RECUT_AGENT_ID=my-service            # default agent_id for recut.init()
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

## Claude & OpenAI Support

- **Claude** — native extended thinking block capture. recut reads the actual internal reasoning Claude produces before each action. The only tool that can detect reasoning-action mismatches at the model level.
- **OpenAI** — inferred reasoning fallback; all other flagging layers apply.

---

## Storage & Privacy

Traces are stored locally in SQLite by default (zero external dependencies). PostgreSQL is supported for multi-process or hosted deployments. PII scrubbing (`RECUT_PII_SCRUB=true`) applies regex redaction to prompts and step content before storage — the in-memory trace is never mutated.

---

See [POSITIONING.md](docs/product/POSITIONING.md) for competitive landscape and use cases.  
See [ROADMAP.md](docs/product/ROADMAP.md) for what's coming.
