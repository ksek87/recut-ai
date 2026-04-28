# recut-ai Demo — Multi-step Research Agent

A real multi-turn Claude agent with tool use, traced and flagged by the recut-ai SDK.

## What it does

Claude analyses NVIDIA as an investment using `get_stock_data` and `web_search` tools.
Extended thinking is enabled, so you see real reasoning steps alongside tool calls and the final output.

| Phase | What happens |
|-------|-------------|
| 1 | Claude runs a multi-turn tool-calling loop; all steps captured as `RecutStep` objects |
| 2 | `recut.peek(trace, flagging_depth="fast")` scores every step (rules + native reasoning analysis); HIGH flags fire the `@recut.on_flag` handler registered at module level |
| 3 | Trace exported to `demo/demo_trace.recut.json` |
| 4 | OTel spans emitted (Console or OTLP) |

The mock provider (offline mode) deliberately scripts two HIGH-severity flags:
- **Step 3:** `anomalous_tool_use` `[rule]` — identical duplicate tool call
- **Step 5:** `reasoning_action_mismatch` `[native]` — uncertain reasoning paired with overconfident output

## Install

```bash
pip install -e ".[demo]"   # adds opentelemetry deps
```

## Run

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
python demo/demo.py
```

Without an API key the demo runs in offline mode using `MockProvider` — useful for testing the SDK without API access.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | — | Required for real Claude runs |
| `RECUT_DEMO_MODEL` | `claude-sonnet-4-6` | Override the Claude model |
| `RECUT_L4_BACKEND` | `local` | Layer 4 judge backend: `local`, `anthropic`, or `openai` |
| `RECUT_L4_LOCAL_URL` | `http://localhost:11434/v1` | Local OpenAI-compatible endpoint (Ollama, LM Studio, etc.) |
| `RECUT_L4_LOCAL_MODEL` | `llama3.2` | Model name for local Layer 4 judge |
| `RECUT_COST_UNIT` | `USD` | Cost display label (e.g. `EUR`, `credits`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | Send spans to a real collector |

## Layer 4 — Bring Your Own Model

Phase 2 uses `flagging_depth="fast"` (layers 1–3 only, zero model cost). To enable the LLM judge:

```python
# Uses local model via Ollama (no API key, no data exfiltration)
audit_record = await recut.peek(trace, flagging_depth="full")

# Or with a remote API key:
# RECUT_L4_BACKEND=anthropic python demo/demo.py
```

If the local endpoint is unreachable, Layer 4 is silently skipped — no error, no cost.

## Visualise in Jaeger

```bash
docker run -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one

ANTHROPIC_API_KEY=sk-ant-... \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
python demo/demo.py

# Open http://localhost:16686 → service: recut-demo
```

## Files

| File | Purpose |
|------|---------|
| `demo.py` | Main demo — real Claude API, 4-phase walkthrough, `@recut.on_flag` handler |
| `mock_provider.py` | Offline `AbstractProvider` — scripted steps that trigger known flags without API access |
| `otel_bridge.py` | Maps `RecutTrace`/`RecutStep`/`RecutFlag` → OTel spans |
