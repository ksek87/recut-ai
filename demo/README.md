# recut-ai Demo — Multi-step Research Agent

A real multi-turn Claude agent with tool use, traced and flagged by the recut-ai SDK.

## What it does

Claude analyses NVIDIA as an investment using two tools (`search_financial_data`, `compare_competitors`) with hardcoded responses — so only the Claude API is needed, no external data services.

Extended thinking is enabled, so you see real reasoning steps alongside tool calls and the final output.

| Phase | What happens |
|-------|-------------|
| 1 | Claude runs a multi-turn tool-calling loop; all steps captured |
| 2 | `FlaggingEngine` scores every step (rules + native reasoning analysis) |
| 3 | Trace exported to `demo/demo_trace.recut.json` |
| 4 | OTel spans emitted (Console or OTLP) |

## Install

```bash
pip install -e ".[demo]"   # adds opentelemetry deps
```

## Run

```bash
ANTHROPIC_API_KEY=sk-ant-... python demo/demo.py
```

Without an API key the demo runs in offline mode using `MockProvider` — useful for testing the SDK integration without API access.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | — | Required for real Claude runs |
| `RECUT_DEMO_MODEL` | `claude-sonnet-4-6` | Override the Claude model |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | Send spans to a real collector |

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
| `demo.py` | Main demo — real Claude API, 4-phase walkthrough |
| `mock_provider.py` | Offline `AbstractProvider` (scripted steps, no API key) |
| `otel_bridge.py` | Maps `RecutTrace`/`RecutStep`/`RecutFlag` → OTel spans |
