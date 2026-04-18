# recut-ai

**Intercept, replay, and audit your AI agent runs.**

recut wraps any agent with a one-line decorator and gives you five modes of interaction: peek inside a run, intercept it mid-flight, replay from any step with injected context, audit the full trace, or stress-test it with auto-generated variants.

Works with Claude and OpenAI. No ML background required.

---

## Install

```bash
pip install recut-ai
```

## Setup

```bash
cp .env.example .env
# Add your ANTHROPIC_API_KEY
```

## Quick Start

```python
import recut

@recut.trace(agent_id="my-agent", mode="peek")
async def run_agent(prompt: str, ctx=None) -> str:
    async for step in ctx.provider.run_agent(prompt):
        ctx.add_step(step)
    return ctx.trace.steps[-1].content

# React to flags in real time
@recut.on_flag
def handle_flag(event: recut.RecutFlagEvent):
    print(f"[{event.flag.severity}] {event.flag.plain_reason}")
```

## CLI

```
recut run "prompt"                        # run an agent, mode defaults to peek
recut peek   <trace-id>                   # triage a recorded trace
recut audit  <trace-id>                   # full structured audit pass
recut intercept "prompt"                  # pause mid-run on high-severity flags
recut replay <trace-id> --step 4          # fork from step 4 with injected context
recut diff   <trace-id> <fork-id>         # side-by-side behavioral diff
recut stress <trace-id> --variants 5      # auto-generate stress variants
recut export <trace-id>                   # export to .recut.json
```

## Modes

| Mode      | What it does                                              |
|-----------|-----------------------------------------------------------|
| Peek      | Fast triage — surfaces high-risk steps only               |
| Audit     | Full structured pass with AuditRecord output              |
| Intercept | Pause mid-run, inspect, redirect                          |
| Replay    | Fork at any step, inject context, run forward             |
| Stress    | Auto-generate adversarial variants from flagged steps     |

## Flags Detected

`overconfidence` · `goal_drift` · `scope_creep` · `reasoning_gap` · `uncertainty_suppression` · `instruction_deviation` · `anomalous_tool_use` · `reasoning_action_mismatch` (Claude native)

Flags are surfaced in plain language — no ML jargon.

---

See [ROADMAP.md](ROADMAP.md) for what's coming.
