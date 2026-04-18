# recut-ai

When your AI agent fails in production, you have no replay button. You see the final output — or the bill — but not why it went wrong or when.

recut gives you that replay button. Wrap any agent with one decorator and get five primitives: **peek** inside a run, **intercept** it mid-flight, **replay** from any step with different inputs, **audit** the full trace, or **stress-test** it with auto-generated variants.

Works with Claude (including native thinking block capture) and OpenAI. No ML background required.

---

## Install

```bash
pip install recut-ai
```

```bash
cp .env.example .env  # add ANTHROPIC_API_KEY
```

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

## Flags

Every flag ships with a plain-language reason — no ML jargon.

`overconfidence` · `goal_drift` · `scope_creep` · `reasoning_gap` · `uncertainty_suppression` · `instruction_deviation` · `anomalous_tool_use` · `reasoning_action_mismatch`

The last one is Claude-only: detected when the agent's private reasoning expresses uncertainty but its action expresses confidence.

---

Already using LangSmith, Langfuse, or W&B? recut sends reasoning data and behavioral flags into your existing traces — nothing to replace. See [INTEGRATIONS.md](docs/product/INTEGRATIONS.md).

See [ROADMAP.md](docs/product/ROADMAP.md) for what's coming.
