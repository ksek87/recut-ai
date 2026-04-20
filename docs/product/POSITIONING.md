# recut-ai — Product Positioning

---

## One-liner

**recut is the debugging layer for AI agents — intercept, replay, and audit any run without touching your agent code.**

---

## The Problem

When an AI agent fails in production, there is no replay.

Engineers see the final output, the bill, or an alert. They don't see *why* the agent made each decision, when it started going wrong, or what would have happened if step 4 had returned a different result. The only debugging tool is re-running the whole thing and hoping.

This is getting worse, not better. Documented failure modes are now well-established:

- **Infinite tool loops.** A Claude Code sub-agent consumed 27M tokens in a single run over 4.6 hours. Zed IDE logged a public case of an agent stuck in "Let me verify…" degeneration. A developer on dev.to described their agent firing 50,000 API requests before anyone noticed. No early warning, no way to pause.
- **Irreversible actions with no review gate.** Replit's agent, told not to touch production during a code freeze, executed `DROP DATABASE` after a sequence of "individually reasonable" decisions. Amazon's Kiro agent autonomously deleted a production AWS environment, causing a 13-hour outage. The reasoning chain looked fine at each step.
- **Silent cascade failures.** Agents return partial answers or loop on the same tool call without surfacing errors. In one documented case, an agent interpreted an empty API response as success while the DB connection pool was exhausted — processing 500+ transactions on incomplete data before detection. Logs showed "decision: approved" with no reasoning.
- **Schema drift.** A dependency upgrade changed tool schema generation silently and broke production agents at multiple companies simultaneously. Nobody could reconstruct what the agent had been doing before the break.

The scale is confirmed: IDC research shows 92% of businesses implementing agentic AI experience cost overruns; 87% of those stem from granting too much autonomy without review gates. AICosts.ai found 73% of teams are "one prompt away from a budget disaster." Galileo's analysis puts inference at only 20% of agentic AI TCO — the rest is governance, oversight, and recovery.

The common thread: **no structured trace, no replay, no gate before action.** Builders can't reconstruct the agent's decision path after the fact — and they can't intercept it before it does something irreversible. As one field report put it: *"logging outputs without reasoning fails every major compliance framework."*

---

## Who This Is For

**Primary: The AI engineer / product builder**

Not an ML researcher. A software generalist — often a senior engineer or technical PM — who integrates LLMs into product workflows. They're shipping agents for ops automation, document processing, customer workflows, or internal tooling. They use Claude or OpenAI APIs directly. They don't need another ML observability dashboard; they need a debugging primitive that works like the rest of their dev toolchain.

Gartner projects 40% of enterprise applications will have embedded agents by 2026. Most of the engineers building those agents are not ML engineers.

**Secondary: Enterprise compliance / AI governance teams**

The EU AI Act (Article 14) and NIST AI RMF both require demonstrable human oversight for high-risk AI systems. "Human-on-the-loop" is now a compliance concept, not just a UX preference. Teams in finance, legal, healthcare, and ops automation need structured audit trails of agent behavior — not just logs.

**Tertiary: Security / red team engineers**

OWASP published the *Top 10 for Agentic Applications* (Dec 2025). MITRE ATLAS added 14 agent-specific attack techniques (Oct 2025). Red teaming agentic stacks is now a named practice. recut's stress mode speaks directly to this workflow.

---

## Competitive Landscape

| Tool | What it does | Reasoning capture | Replay / fork | Mid-run intercept | Behavioral flags |
|------|-------------|-------------------|---------------|-------------------|-----------------|
| **LangSmith** | Tracing + evals for LangChain | No | No | No | No |
| **Langfuse** | Open-source LLM observability | No | No | No | No |
| **Helicone** | API proxy logging | No | No | No | No |
| **Arize Phoenix** | ML observability, traces + evals | No | No | No | No |
| **Weights & Biases Weave** | Experiment tracking + LLM traces | No | No | No | No |
| **Braintrust** | Evaluation platform | No | No | No | No |
| **Fiddler AI** | Model monitoring + LLM guardrails | No | No | No | Partial |
| **Portkey** | API gateway, routing + caching | No | No | No | No |
| **recut-ai** | Intercept, replay, audit | **Yes (Claude native)** | **Yes** | **Yes** | **Yes** |

**The gap:** Every existing tool is an *observer* — including Fiddler, which has the most sophisticated LLM monitoring of the group. They log and alert on what happened. None of them let you fork a run from a specific step, gate an action pending review, or capture and compare the agent's reasoning against its actions. recut is the first tool in this space built as a *debugger and control layer*, not a dashboard. And critically, recut feeds into all of them — adding the reasoning and behavioral signal layer they're missing.

---

## Key Differentiators

**1. Native reasoning capture (Claude) — the moat**
Claude's extended thinking blocks expose real internal reasoning — not a summary, not an inference. recut captures thinking tokens per step and flags `reasoning_action_mismatch`: when the agent's private reasoning expresses uncertainty but its action expresses confidence. This signal is mechanistic — it comes from the model itself, not from a second LLM judging it. No other tool in the market produces it.

**2. Replay from any step**
Fork a trace at step 4, inject a different tool result, run forward. See exactly how the agent's behavior changes. No other tool in the space does this.

**3. Intercept mid-run**
Pause execution the moment a high-severity flag fires. Inspect the trace, redirect the agent, or abort. Pairs directly with the human-on-the-loop compliance requirement.

**4. Layered detection — not "AI judging AI"**
The flagging engine has four layers. The first three — rule-based, embedding similarity, and native thinking analysis — use no meta-LLM at all. The LLM judge (layer 4) fires only on steps that pass the first three, and only in full audit mode. Engineers can run in `flagging_depth="fast"` for instant, zero-cost flagging (layers 1-3 only) and opt into layer 4 for compliance audit passes. Every flag shows which layer fired it: `[rule]`, `[embedding]`, `[native]`, or `[judge]`.

**5. Behavioral fingerprinting from your own history**
After enough runs, recut builds a per-agent statistical baseline from traces in your local store. New runs are compared to the baseline with Z-score anomaly detection — no model, no API, no opinion. "This run used 3.1σ more tool calls than baseline" is a deterministic mathematical signal.

**6. Cost attribution per step**
Every step carries `token_cost_usd`. Every trace carries aggregate cost. Engineers see exactly how much each decision cost and where budget went before a failure. "This agent ran $4.20 before the goal_drift flag fired at step 7" — visible in `peek` output.

**7. Behavioral flags in plain language**
Every flag has a `plain_reason`: *"The agent seemed unsure in its thinking but acted confidently anyway — worth a closer look."* Written for engineers and compliance reviewers, not ML researchers.

**8. Zero ML background required**
The entire surface — CLI, TUI, flags, audit records — is designed for product engineers. No embeddings dashboard, no loss curves, no ML jargon.

---

## Messaging Pillars

**"When your agent breaks, you need a replay button."**
Not another dashboard. A debugging primitive. Intercept the run, fork from the moment it went wrong, test the fix — without re-running the whole thing from scratch.

**"See what your agent was actually thinking."**
For Claude users: native reasoning block capture shows you the internal deliberation before each action. Flag the gap between what the agent thought and what it did.

**"Gate the actions that matter. Let everything else run."**
Not every agent step needs a reviewer. Intercept fires only when behavioral flags cross your threshold — on high-severity tool calls, scope creep, or confidence mismatches. Everything else runs at full speed. Your agent isn't slower; it's just accountable where it counts.

**"Stress test before your users do."**
Auto-generate adversarial variants from flagged steps. Find the edge cases that break your agent's reasoning before they hit production.

---

## Use Cases

**Debugging a production failure**
Agent failed mid-run. Load the trace, peek at high-risk steps, find the divergence point, replay from there with corrected context. Root cause in minutes, not hours of log archaeology.

**Pre-release audit**
Before shipping a new agent version, run audit mode on a representative trace set. Get a structured AuditRecord with risk profile, flag counts, and behavioral summary. Export as `.recut.json` for your compliance record.

**Action review gate for irreversible decisions**
Register an `on_flag` hook that fires before any tool call above `HIGH` severity — database writes, external API calls, file deletions. Pause the run, surface the reasoning and flag context to a reviewer, then resume or redirect. Structured decision provenance is recorded automatically. Meets EU AI Act Article 14 and NIST AI RMF oversight requirements without redesigning your agent.

**Red team / stress testing**
Take a trace where a high-risk flag fired. Run stress mode with 5 variants — auto-generated with amplified uncertainty, contradicted tool results, or adversarial inputs. Get a stability verdict per variant. Know whether your agent's reasoning holds under pressure before your attacker finds out it doesn't.

**Cost runaway prevention**
Intercept mode + `reasoning_loop` flag catches repeated identical tool calls before they spiral. The flag fires at Layer 1 (rule-based, zero cost) the moment the pattern appears.

---

## Common Objection: "Why Not Just Use IDE Checkpointing?"

This is a category mismatch. IDE checkpointing (as in Claude Code, Cursor, or Zed) saves developer session state during development. recut captures production agent decision traces at runtime.

| Dimension | IDE Checkpointing | recut |
|---|---|---|
| **When** | Development time | Production runtime |
| **What's saved** | File system / session state | Decision + reasoning state |
| **Replay** | Resume the developer's session | Fork the agent's reasoning chain from any step |
| **Analysis** | None — just restore | Behavioral flag scoring, cost attribution |
| **Trigger** | Developer manually checkpoints | Agent behavior triggers the alert |
| **Intercept** | N/A | Pause mid-run before an irreversible action |
| **Stress testing** | N/A | Auto-generate adversarial variants from real failures |
| **Compliance** | N/A | Structured audit trail with human review sign-off |

**The framing that sticks:** IDE checkpointing is a save game. recut is a flight data recorder plus air traffic control. One is for the developer. One is for the deployed system.

---

## What recut Is Not

- Not a logging platform. It doesn't replace your existing telemetry.
- Not an evaluation framework. It doesn't run evals on benchmark datasets.
- Not an ML observability tool. No loss curves, no model performance metrics.
- Not a proxy. It doesn't sit in your API call path.
- Not "AI judging AI." Layers 1-3 of the flagging engine use no meta-LLM — only rules, math, and the model's own thinking blocks.

recut wraps your agent at the function level. One decorator. It is additive.

---

## Market Timing

The conditions are right now:

1. **Agentic failures are public and quantified.** 92% of businesses report cost overruns from agentic AI. 73% of teams self-report being "one prompt away from a budget disaster." The pain is real, visible, and getting press.
2. **Regulatory pressure is creating a compliance pull.** EU AI Act Article 14, NIST AI RMF, SOC 2, HIPAA, and ISO 27001 auditors are now asking about agent decision provenance. IBM frames this as the need for "agent decision records (ADRs)" — structured logs of *why* an agent acted, not just *what* it did. recut produces exactly this.
3. **Claude's extended thinking API is a narrow window.** Native reasoning capture is only possible because Anthropic exposed thinking blocks. No other tool in the market captures them. That window won't stay open indefinitely.
4. **The "AI engineer" persona is now mainstream.** SF Standard declared "engineer is so 2025 — in AI land, everyone's a builder now." Gartner projects 40% of enterprise apps will have embedded agents by 2026. Most builders are not ML engineers; none of the existing ML observability tools are built for them.
5. **Existing platforms are observer-only.** Even Fiddler — the most capable LLM monitoring platform — has no replay, no pre-action gate, no reasoning trace export. The market is wide open for a tool that acts as a *control layer*, not just a dashboard.

The observability tools that exist were built when LLMs were stateless chat. Agents are stateful, multi-step, and capable of irreversible actions. The tooling hasn't caught up.
