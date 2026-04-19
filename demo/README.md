# recut-ai Demo

End-to-end walkthrough of the recut-ai SDK using a scripted "Biased Research Agent" — no `ANTHROPIC_API_KEY` required.

## What It Demonstrates

| Phase | Feature | What happens |
|-------|---------|--------------|
| 1 | Build trace | MockProvider yields 6 scripted steps |
| 2 | `peek()` | FlaggingEngine scores all steps; 2 HIGH flags fire |
| 3 | `intercept()` | Live per-step flag callback during a second run |
| 4 | `replay()` | Corrected step injected at the duplicate tool call |
| 5 | `stress()` | 3 identical runs compared for flag consistency |
| 6 | `export()` | Trace written to `demo/demo_trace.recut.json` |
| 7 | OTel | Spans emitted via ConsoleSpanExporter (or OTLP) |

## Expected Flags

- **Step 3** → `ANOMALOUS_TOOL_USE HIGH` — agent calls the same tool with identical inputs twice (Layer 1 rule)
- **Step 5** → `REASONING_ACTION_MISMATCH HIGH` — reasoning block expresses uncertainty; output is overconfident (Layer 3 native mismatch)

## Install

```bash
# From repo root
pip install -e ".[demo]"
```

Core dependencies (`pydantic`, `anthropic`, `rich`) are already included in the base install.  
The `[demo]` extra adds `opentelemetry-api`, `opentelemetry-sdk`, and `opentelemetry-exporter-otlp-proto-grpc`.

## Run

```bash
python demo/demo.py
```

## Send Spans to a Real Collector (optional)

```bash
# Start Jaeger all-in-one
docker run -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one

# Run demo with OTLP export
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python demo/demo.py

# Open http://localhost:16686 and search for service "recut-demo"
```

## Files

| File | Purpose |
|------|---------|
| `mock_provider.py` | `AbstractProvider` implementation with scripted steps |
| `otel_bridge.py` | Maps `RecutTrace`/`RecutStep`/`RecutFlag` → OTel spans |
| `demo.py` | Top-level orchestration (7 phases) |
