# recut-ai — Product Positioning

---

## One-liner

**recut is the debugging layer for AI agents — intercept, replay, and audit any run without touching your agent code.**

---

## The Problem

When an AI agent fails in production, there is no replay.

Engineers see the final output, the bill, or an alert. They don't see *why* the agent made each decision, when it started going wrong, or what would have happened if step 4 had returned a different result. The only debugging tool is re-running the whole thing and hoping.

This is getting worse, not better. Documented failure modes are now well-established:

- **Infinite tool loops.** A Claude Code sub-agent consumed 27M tokens in a single run over 4.6 hours. Zed IDE logged a public case of an agent stuck in "Let me verify…" degeneration. No early warning, no way to pause.
- **Scope creep that looks reasonable mid-run.** Replit's agent, told not to touch production during a code freeze, executed `DROP DATABASE` after a sequence of "individually reasonable" decisions. The reasoning chain looked fine at each step.
- **Silent failures.** Agents return partial answers or loop on the same tool call without surfacing errors. The first visible signal is often the token bill.
- **Schema drift.** A dependency upgrade changed tool schema generation silently and broke production agents at multiple companies simultaneously. Nobody could reconstruct what the agent had been doing before the break.

The common thread: **no structured trace, no replay, no intercept.** Builders can't reconstruct the agent's decision path after the fact — and they can't pause it before it does something irreversible.

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
| **Portkey** | API gateway, routing + caching | No | No | No | No |
| **recut-ai** | Intercept, replay, audit | **Yes (Claude native)** | **Yes** | **Yes** | **Yes** |

**The gap:** Every existing tool is an *observer*. They log what happened after the fact. None of them let you fork a run from a specific step, pause mid-execution, or capture and compare the agent's reasoning against its actions. recut is the first tool in this space built as a *debugger*, not a dashboard.

---

## Key Differentiators

**1. Replay from any step**
Fork a trace at step 4, inject a different tool result, run forward. See exactly how the agent's behavior changes. No other tool in the space does this.

**2. Native reasoning capture (Claude)**
Claude's extended thinking blocks expose real internal reasoning — not a summary, not an inference. recut captures thinking tokens per step and flags `reasoning_action_mismatch`: when the agent's private reasoning expresses uncertainty but its action expresses confidence. This is a signal no other tool can produce for Claude.

**3. Intercept mid-run**
Pause execution the moment a high-severity flag fires. Inspect the trace, redirect the agent, or abort. Pairs directly with the human-on-the-loop compliance requirement.

**4. Behavioral flags in plain language**
Every flag has a `plain_reason`: *"The agent seemed unsure in its thinking but acted confidently anyway — worth a closer look."* Written for engineers and compliance reviewers, not ML researchers.

**5. Zero ML background required**
The entire surface — CLI, TUI, flags, audit records — is designed for product engineers. No embeddings dashboard, no loss curves, no ML jargon.

---

## Messaging Pillars

**"When your agent breaks, you need a replay button."**
Not another dashboard. A debugging primitive. Intercept the run, fork from the moment it went wrong, test the fix — without re-running the whole thing from scratch.

**"See what your agent was actually thinking."**
For Claude users: native reasoning block capture shows you the internal deliberation before each action. Flag the gap between what the agent thought and what it did.

**"Human-on-the-loop, not human-in-the-way."**
Compliance and governance without blocking your agent on every step. Intercept fires only when behavioral flags cross your threshold. Everything else runs uninterrupted.

**"Stress test before your users do."**
Auto-generate adversarial variants from flagged steps. Find the edge cases that break your agent's reasoning before they hit production.

---

## Use Cases

**Debugging a production failure**
Agent failed mid-run. Load the trace, peek at high-risk steps, find the divergence point, replay from there with corrected context. Root cause in minutes, not hours of log archaeology.

**Pre-release audit**
Before shipping a new agent version, run audit mode on a representative trace set. Get a structured AuditRecord with risk profile, flag counts, and behavioral summary. Export as `.recut.json` for your compliance record.

**HITL gate for irreversible actions**
Register an `on_flag` hook that fires before any tool call above `HIGH` severity. Pause the agent, surface the flag to a human reviewer, resume or redirect. Meets EU AI Act Article 14 oversight requirements without redesigning your agent.

**Red team / stress testing**
Take a trace where a high-risk flag fired. Run stress mode with 5 variants — auto-generated with amplified uncertainty, contradicted tool results, or adversarial inputs. Get a stability verdict per variant. Know whether your agent's reasoning holds under pressure before your attacker finds out it doesn't.

**Cost runaway prevention**
Intercept mode + `reasoning_loop` flag catches repeated identical tool calls before they spiral. The flag fires at Layer 1 (rule-based, zero cost) the moment the pattern appears.

---

## What recut Is Not

- Not a logging platform. It doesn't replace your existing telemetry.
- Not an evaluation framework. It doesn't run evals on benchmark datasets.
- Not an ML observability tool. No loss curves, no model performance metrics.
- Not a proxy. It doesn't sit in your API call path.

recut wraps your agent at the function level. One decorator. It is additive.

---

## Market Timing

The conditions are right now:

1. Agentic failures are public and documented — the pain is real and visible.
2. Regulatory pressure (EU AI Act, NIST AI RMF) is creating a compliance pull, not just a developer pull.
3. Claude's extended thinking API is new — native reasoning capture is only possible because Anthropic exposed it. The window to be first in this space is open.
4. The "AI engineer" persona has consolidated — there are now millions of product engineers building with LLM APIs who aren't served by ML observability tools.

The observability tools that exist were built when LLMs were stateless chat. Agents are stateful, multi-step, and capable of irreversible actions. The tooling hasn't caught up.
