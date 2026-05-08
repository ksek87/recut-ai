# Configuration Reference

All configuration is via environment variables and Python API parameters. There is no config file — set variables in your shell, `.env`, or CI secrets.

---

## API Keys

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | When using Claude | Claude API key — [console.anthropic.com](https://console.anthropic.com) |
| `OPENAI_API_KEY` | When using OpenAI or `RECUT_L4_BACKEND=openai` | OpenAI API key |

---

## `@recut.trace()` — decorator parameters

```python
@recut.trace(
    agent_id="my-agent",      # string identifier shown in CLI output and audit records
    mode="peek",               # "peek" | "audit" | "intercept" | "stress" (default: "peek")
    language="simple",         # "simple" (plain English) | "power" (technical detail)
    provider=None,             # AbstractProvider instance; defaults to AnthropicProvider
    sample_rate=1.0,           # fraction of calls to trace (0.0–1.0); overridden by RECUT_DEFAULT_SAMPLE_RATE
    trace_if=None,             # callable(RecutContext) -> bool; skip tracing if it returns False
    flag_handlers=None,        # list of handler callables local to this trace (in addition to global @on_flag handlers)
)
async def run_agent(prompt: str, ctx=None) -> str:
    ...
```

`ctx` is injected automatically as a keyword argument. The wrapped function must accept `ctx=None`.

### `mode` values

| Mode | Behaviour |
|---|---|
| `"peek"` | Fast triage — layers 1–3 only (zero model cost), surfaces high-risk steps |
| `"audit"` | Full audit — all four layers, produces an `AuditRecord` suitable for compliance review |
| `"intercept"` | Pauses execution when a high-severity flag fires; use with `recut intercept` CLI |
| `"stress"` | Run context for stress-test variants; normally set automatically by `recut.stress()` |

### `language` values

| Language | Summaries produced by |
|---|---|
| `"simple"` | Plain English, non-technical; suitable for PMs and analysts |
| `"power"` | Technical detail; includes step type, flag type, and layer labels |

---

## `@recut.on_flag()` — global flag handlers

Register a function to be called whenever a flag fires, across all active traces.

```python
# Bare decorator — fires on every flag
@recut.on_flag
def handle_all(event: recut.RecutFlagEvent):
    print(event.flag.plain_reason)

# Filter by severity
@recut.on_flag(severity="high")
async def alert_on_high(event: recut.RecutFlagEvent):
    await slack.send(f"HIGH flag: {event.flag.plain_reason}")

# Filter by flag type
@recut.on_flag(flag_type="goal_drift")
def watch_drift(event: recut.RecutFlagEvent):
    metrics.increment("recut.goal_drift")

# Both filters combined (AND logic)
@recut.on_flag(severity="high", flag_type="anomalous_tool_use")
def strict_watch(event: recut.RecutFlagEvent):
    ...
```

### `RecutFlagEvent` fields

| Field | Type | Description |
|---|---|---|
| `trace_id` | `str` | ID of the parent trace |
| `step_id` | `str` | ID of the flagged step |
| `flag` | `RecutFlag` | The flag itself (type, severity, reason, source, confidence, evidence) |
| `suggested_action` | `str` | Plain-language suggested response |
| `preceding_steps` | `list[RecutStep]` | Up to 2 steps before the flagged step |
| `agent_id` | `str` | The agent ID from `@recut.trace()` |

### `RecutFlag` fields

| Field | Type | Description |
|---|---|---|
| `type` | `FlagType` | One of the eight flag types (see flags table in README) |
| `severity` | `Severity` | `"low"` \| `"medium"` \| `"high"` |
| `plain_reason` | `str` | Human-readable explanation |
| `source` | `FlagSource` | `"rule"` \| `"embedding"` \| `"native"` \| `"llm"` |
| `confidence` | `float \| None` | Judge confidence 0–1 (Layer 4 only) |
| `evidence` | `str \| None` | Short quote from the step that triggered the flag (Layer 4 only) |
| `step_id` | `str` | ID of the step that was flagged |

---

## Layer 4 Judge (BYOM)

Layer 4 sends a batch of ambiguous steps to a language model for judgment. It defaults to a **local model** — no API cost, no data leaves your machine.

| Variable | Default | Description |
|---|---|---|
| `RECUT_L4_BACKEND` | `local` | `local` \| `anthropic` \| `openai` |
| `RECUT_L4_LOCAL_URL` | `http://localhost:11434/v1` | Base URL for local OpenAI-compatible endpoint (Ollama, LM Studio, llama.cpp, vLLM) |
| `RECUT_META_MODEL` | model-dependent | Override the model for all backends at once |
| `RECUT_META_MODEL_ANTHROPIC` | `claude-haiku-4-5-20251001` | Default model when `RECUT_L4_BACKEND=anthropic` |
| `RECUT_META_MODEL_OPENAI` | `gpt-4o-mini` | Default model when `RECUT_L4_BACKEND=openai` |
| `RECUT_META_MODEL_LOCAL` | `llama3` | Default model when `RECUT_L4_BACKEND=local` |
| `RECUT_L4_MAX_TOKENS` | `2000` | Maximum tokens the judge can emit per response |
| `RECUT_L4_CONTENT_TRUNCATE` | `500` | Characters of step content sent to the judge |
| `RECUT_L4_REASONING_TRUNCATE` | `300` | Characters of step reasoning sent to the judge |
| `RECUT_L4_PROMPT_TRUNCATE` | `300` | Characters of the original prompt sent to the judge |
| `RECUT_L4_EVIDENCE_TRUNCATE` | `200` | Characters of quoted evidence stored per flag |
| `RECUT_L4_RETRY_ATTEMPTS` | `3` | API call retry attempts before giving up |
| `RECUT_L4_RATELIMIT_BACKOFF` | `5.0` | Base seconds for rate-limit backoff (`base × attempt`) |
| `RECUT_L4_CONNECTION_BACKOFF` | `2.0` | Base seconds for connection-error backoff (`base ^ attempt`) |

`RECUT_META_MODEL` overrides per-backend defaults. Per-backend vars (`RECUT_META_MODEL_ANTHROPIC` etc.) let you set different defaults for each backend without locking all three to the same model.

### Backend defaults

| Backend | Default model | Notes |
|---|---|---|
| `local` | `llama3` | Any model served at `RECUT_L4_LOCAL_URL`. Silently skipped if endpoint unreachable |
| `anthropic` | `claude-haiku-4-5-20251001` | Requires `ANTHROPIC_API_KEY` |
| `openai` | `gpt-4o-mini` | Requires `OPENAI_API_KEY` |

### `flagging_depth`

Pass to `recut.peek()`, `recut.audit()`, or `FlaggingEngine` directly:

```python
record = await recut.audit(trace, flagging_depth="fast")   # layers 1–3 only (default for peek)
record = await recut.audit(trace, flagging_depth="full")   # all 4 layers (default for audit)
```

---

## Token Costs

recut tracks token usage and computes cost per step and per trace. Costs are stored in the configured unit and displayed in `recut peek` and the audit TUI.

| Variable | Default | Description |
|---|---|---|
| `RECUT_PRICE_INPUT` | *(built-in table)* | Input token price per million tokens in your billing unit |
| `RECUT_PRICE_OUTPUT` | *(built-in table)* | Output token price per million tokens in your billing unit |
| `RECUT_COST_UNIT` | `USD` | Display label for costs — set to `EUR`, `GBP`, `credits`, etc. |

When `RECUT_PRICE_INPUT` and `RECUT_PRICE_OUTPUT` are both set, they override the built-in model pricing table entirely. This covers:

- Enterprise discounts or negotiated rates
- Non-USD billing currencies (set the price in your currency, set `RECUT_COST_UNIT` to match)
- Credits-based billing systems

```bash
# Example: discounted Anthropic rate in EUR
RECUT_PRICE_INPUT=2.10    # €2.10 per million input tokens
RECUT_PRICE_OUTPUT=10.50  # €10.50 per million output tokens
RECUT_COST_UNIT=EUR
```

If a model is not in the built-in table and no env var override is set, cost fields are `None` (no display, no crash).

### Built-in pricing tables

**Anthropic**

| Model | Input ($/M) | Output ($/M) |
|---|---|---|
| `claude-opus-4-7` | 15.00 | 75.00 |
| `claude-sonnet-4-6` | 3.00 | 15.00 |
| `claude-haiku-4-5` / `claude-haiku-4-5-20251001` | 0.80 | 4.00 |
| `claude-3-5-sonnet-20241022` | 3.00 | 15.00 |
| `claude-3-5-haiku-20241022` | 0.80 | 4.00 |
| `claude-3-opus-20240229` | 15.00 | 75.00 |

**OpenAI** (date-suffixed variants like `gpt-4o-2024-11-20` resolve automatically)

| Model | Input ($/M) | Output ($/M) |
|---|---|---|
| `gpt-4o` | 2.50 | 10.00 |
| `gpt-4o-mini` | 0.15 | 0.60 |
| `gpt-4-turbo` | 10.00 | 30.00 |
| `gpt-4` | 30.00 | 60.00 |
| `gpt-3.5-turbo` | 0.50 | 1.50 |
| `o1` | 15.00 | 60.00 |
| `o1-mini` | 3.00 | 12.00 |
| `o3-mini` | 1.10 | 4.40 |

---

## Flagging Engine Tuning

| Variable | Default | Description |
|---|---|---|
| `RECUT_USE_EMBEDDINGS` | `true` | Enable Layer 2 embedding similarity (requires `sentence-transformers`) |
| `RECUT_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model used for Layer 2 |
| `RECUT_EMBEDDING_BATCH_SIZE` | `32` | Batch size for embedding encoding |
| `RECUT_EMBEDDING_THRESHOLD` | `0.75` | Cosine similarity threshold for Layer 2 drift detection |
| `RECUT_SCOPE_CREEP_THRESHOLD` | `20` | Step index above which a SCOPE_CREEP flag fires |
| `RECUT_CONTEXT_WINDOW_SIZE` | `2` | Number of preceding steps used for context in flagging and caching |
| `RECUT_FLAG_THRESHOLD_LOW` | `0.4` | Minimum score for a LOW-severity flag |
| `RECUT_FLAG_THRESHOLD_MEDIUM` | `0.65` | Minimum score for a MEDIUM-severity flag |
| `RECUT_FLAG_THRESHOLD_HIGH` | `0.85` | Minimum score for a HIGH-severity flag |
| `RECUT_RISK_WEIGHT_LOW` | `0.3` | Risk score assigned to LOW-severity flags |
| `RECUT_RISK_WEIGHT_MEDIUM` | `0.6` | Risk score assigned to MEDIUM-severity flags |
| `RECUT_RISK_WEIGHT_HIGH` | `1.0` | Risk score assigned to HIGH-severity flags |
| `RECUT_FINGERPRINT_HISTORY_LIMIT` | `50` | Historical traces loaded per agent for behavioral fingerprinting |

Scores below `RECUT_FLAG_THRESHOLD_LOW` are silently discarded.

---

## Flag Caching

Layer 1–4 results are cached by content hash to avoid re-scoring identical steps across replay variants.

| Variable | Default | Description |
|---|---|---|
| `RECUT_CACHE_ENABLED` | `true` | Set to `false` to disable all flag caching |
| `RECUT_CACHE_TTL` | `3600` | Cache entry lifetime in seconds |

---

## Sampling & Performance

| Variable | Default | Description |
|---|---|---|
| `RECUT_DEFAULT_SAMPLE_RATE` | `1.0` | Global override for `sample_rate` in all `@recut.trace()` decorators |
| `RECUT_API_TIMEOUT` | `60.0` | Read/write HTTP timeout in seconds for all provider and Layer 4 API calls |
| `RECUT_API_CONNECT_TIMEOUT` | `10.0` | TCP connection timeout in seconds |

Sampling example — trace 10% of production calls:

```bash
RECUT_DEFAULT_SAMPLE_RATE=0.1
```

---

## Stress Testing

| Variable | Default | Description |
|---|---|---|
| `RECUT_STRESS_VARIANTS` | `3` | Maximum number of injection variants generated per `recut.stress()` call |
| `RECUT_STRESS_CONCURRENCY` | `3` | Maximum variants running in parallel (semaphore limit) |
| `RECUT_STRESS_FAILED_THRESHOLD` | `0.8` | `fork_risk` score at or above which a variant is marked `FAILED` |
| `RECUT_STRESS_DEGRADED_THRESHOLD` | `0.2` | `risk_delta` above which a variant is marked `DEGRADED` (below failed threshold) |

## Replay & Diff

| Variable | Default | Description |
|---|---|---|
| `RECUT_DIFF_RISK_DELTA` | `0.1` | Absolute risk delta below which the diff summary reads "little visible effect" |

## Storage

| Variable | Default | Description |
|---|---|---|
| `RECUT_DB_PATH` | `~/.recut/recut.db` | Local SQLite database path |
| `RECUT_DB_URL` | *(unset)* | PostgreSQL connection string — overrides `RECUT_DB_PATH` when set |
| `RECUT_CB_THRESHOLD` | `5` | Consecutive storage failures before the circuit breaker trips |
| `RECUT_CB_COOLDOWN` | `60` | Seconds before the circuit breaker resets after tripping |
| `RECUT_WRITE_QUEUE_MAXSIZE` | `0` | Max pending writes in the async queue (`0` = unlimited) |
| `RECUT_WRITE_QUEUE_DRAIN_TIMEOUT` | `30.0` | Seconds to wait when draining the queue on shutdown |

When the circuit breaker is open, trace persistence is skipped silently — the agent continues running.

All writes are serialized through a background worker queue, preventing SQLite write contention under concurrent traces.

## PII Scrubbing

Opt-in PII scrubbing redacts sensitive values before storing step content and prompts in SQLite.

| Variable | Default | Description |
|---|---|---|
| `RECUT_PII_SCRUB` | `false` | Set to `true` to enable PII scrubbing before storage |
| `RECUT_PII_PATTERNS` | `email,phone,ssn,credit_card,ip_address` | Comma-separated list of patterns to apply |

Matched values are replaced with `[REDACTED]`. Scrubbing applies to step content, reasoning content, and the original prompt stored in SQLite. The in-memory trace object is not mutated.

```bash
# Enable all patterns
RECUT_PII_SCRUB=true

# Enable only email and phone scrubbing
RECUT_PII_SCRUB=true
RECUT_PII_PATTERNS=email,phone
```

---

## Provider Setup

### AnthropicProvider

```python
from recut.providers.anthropic import AnthropicProvider

provider = AnthropicProvider(
    model="claude-sonnet-4-6",   # any Claude model
    api_key=None,                 # falls back to ANTHROPIC_API_KEY
    thinking_budget=10_000,       # max tokens for extended thinking blocks
)
```

### OpenAIProvider

```python
from recut.providers.openai import OpenAIProvider

provider = OpenAIProvider(
    model="gpt-4o",        # any OpenAI model
    api_key=None,           # falls back to OPENAI_API_KEY
    infer_reasoning=True,   # reconstruct reasoning via a cheap meta-LLM call
)
```

Pass to `@recut.trace()`:

```python
@recut.trace(agent_id="my-agent", provider=OpenAIProvider(model="gpt-4o-mini"))
async def run_agent(prompt: str, ctx=None) -> str:
    ...
```

---

## Full `.env` Example

```bash
# API keys
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Layer 4 — use local Ollama by default, no API cost
RECUT_L4_BACKEND=local
RECUT_L4_LOCAL_URL=http://localhost:11434/v1
# Override the model per backend without locking all backends to one value
RECUT_META_MODEL_ANTHROPIC=claude-haiku-4-5-20251001
RECUT_META_MODEL_OPENAI=gpt-4o-mini
RECUT_META_MODEL_LOCAL=llama3
# Layer 4 resource limits
RECUT_L4_MAX_TOKENS=2000
RECUT_L4_RETRY_ATTEMPTS=3

# Token costs — EUR pricing with discount
RECUT_PRICE_INPUT=2.10
RECUT_PRICE_OUTPUT=10.50
RECUT_COST_UNIT=EUR

# Flagging tuning
RECUT_USE_EMBEDDINGS=true
RECUT_EMBEDDING_MODEL=all-MiniLM-L6-v2
RECUT_EMBEDDING_BATCH_SIZE=32
RECUT_FLAG_THRESHOLD_HIGH=0.80
RECUT_SCOPE_CREEP_THRESHOLD=20
RECUT_CONTEXT_WINDOW_SIZE=2
RECUT_RISK_WEIGHT_LOW=0.3
RECUT_RISK_WEIGHT_MEDIUM=0.6
RECUT_RISK_WEIGHT_HIGH=1.0
RECUT_FINGERPRINT_HISTORY_LIMIT=50

# Stress testing
RECUT_STRESS_VARIANTS=3
RECUT_STRESS_CONCURRENCY=3
RECUT_STRESS_FAILED_THRESHOLD=0.8
RECUT_STRESS_DEGRADED_THRESHOLD=0.2

# Replay diff sensitivity
RECUT_DIFF_RISK_DELTA=0.1

# Production sampling — trace 20% of calls
RECUT_DEFAULT_SAMPLE_RATE=0.2

# Storage
RECUT_DB_PATH=~/.recut/recut.db

# Timeouts
RECUT_API_TIMEOUT=30
RECUT_API_CONNECT_TIMEOUT=10
```
