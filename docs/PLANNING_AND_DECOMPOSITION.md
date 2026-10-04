# Multi-step planning and decomposition, and how to add it to this app

This document explains multi-step planning/decomposition frameworks for LLM
agents — what they are, the handful of shapes they come in, and where (if
anywhere) they'd earn their keep in `backend/ai/`. Like
`docs/AGENTS_AND_MCP_EXPLAINED.md`, it pairs each idea with the real file
that already implements a piece of it, and ends with a phased plan you can
execute later. It does not depend on the other two concept documents
(`FEEDBACK_LOOPS_AND_FINE_TUNING.md`, `GRAPH_BASED_REASONING.md`) and can be
implemented on its own, though §6 notes one cheap synergy with Tool RAG if
you've already built that.

**Headline finding, stated up front so it shapes everything below:** this
app already runs a planning framework — ReAct — inside `run_agent`. The
question this document actually answers is not "should I add planning" but
"is implicit, interleaved planning (what's already built) enough, or does
some task in this app need an *explicit* plan the code can see, audit, and
bound — and if so, which one, and at what latency cost." The honest answer,
argued in §3, is: not yet, for any agent that exists today, but the design
in §5 is worth having ready for the first agent whose job is genuinely
compound.

---

## 1. The taxonomy: four shapes, one spectrum

Every "planning/decomposition framework" you'll read about is a variation
on how much of the plan is decided *before* execution starts vs. discovered
*during* it, and how visible that plan is outside the model's own hidden
reasoning.

```
implicit, no visible plan                                explicit, fully visible plan
──────────────────────────────────────────────────────────────────────────────────────▶
ReAct                    Hardcoded pre-fetch        Plan-and-Execute         Tree/Graph-of-Thought
(interleave              (decomposition fixed        (one upfront plan,      (branch into several
 reason+act,              by the programmer,          then execute each      candidate plans/paths,
 one step at a            not the model — the         step, optionally       score them, keep the
 time, replan             model just decides            replan if a step      best — or merge branches
 implicitly every         *whether* to act on it)       fails)                back together)
 turn)
```

- **ReAct** (Yao et al., 2022) is "think, act, observe, repeat" —
  exactly the `for step in range(max_steps)` loop in
  [`backend/ai/common/agent_loop.py`](../backend/ai/common/agent_loop.py).
  The model never commits to a plan up front; each turn it sees the latest
  tool result and decides the next single action. This is the cheapest
  possible planning framework in tokens and round trips, because there's no
  separate "planning" call — planning and acting are the same call.
- **Hardcoded decomposition** isn't usually listed in the academic
  taxonomy, but it's the most common real-world pattern and this app uses
  it twice: `import_reviewer` and `security_resolver` both decompose "review
  this thing" into fixed sub-steps (*fetch mispriced trades, fetch
  freshness, fetch pending resolutions, render one summary, then ask the
  model to decide*) **in Python, before the model ever runs** — see
  `render_user_message` in both agents. The "plan" is a fixed pipeline the
  programmer wrote once; the model's only decision is what to do with the
  result. This is strictly cheaper than any model-driven planning, because
  it costs zero extra model calls.
- **Plan-and-Execute** (a.k.a. "plan-and-solve") asks the model for an
  explicit multi-step plan *once*, up front, then executes each step (each
  step can itself be a small ReAct loop with its own tool calls), optionally
  feeding failures back into a re-plan. The plan is a real, inspectable
  artifact — a list of subgoals — not just an emergent property of many
  ReAct turns. This is what §5 designs.
- **Tree-of-Thought / Graph-of-Thought** generalize further: instead of one
  plan, generate several candidate plans (or several candidate next steps at
  each point), evaluate them, and keep the best — or, for
  Graph-of-Thought, allow branches to *merge* back together (e.g. two
  sub-analyses combined into one conclusion), not just fan out. These cost
  a multiple of the base latency (you're running several plans/branches,
  not one) and are covered briefly in
  `docs/GRAPH_BASED_REASONING.md` §1c rather than here, because for this
  app's task shapes (mostly linear: fetch → decide → write, or fetch →
  answer) there's no branching decision that plausibly needs more than one
  candidate path evaluated. Don't reach for this without a concrete task
  that has real branch-and-merge structure.

---

## 2. What decomposition already looks like in this codebase

Three real examples, in increasing order of how much "planning" happens at
the model layer vs. the code layer:

| Agent | Decomposition | Where it lives |
|---|---|---|
| `security_resolver` | None — single decision, one rendered summary, one terminal tool call | `render_user_message` in `ai/agents/security_resolver/agent.py` |
| `import_reviewer` | Fixed 3-step pre-fetch (mispriced trades, freshness, pending resolutions), then one decision | `_run_loop` in `ai/agents/import_reviewer/agent.py` |
| `portfolio_assistant` | None fixed — whatever multi-hop reasoning a question needs happens turn-by-turn inside ReAct | `run_agent` called directly from `_run_loop` |

The `import_reviewer` case is worth sitting with, because it's the cleanest
illustration of §4's core lesson before you even add a planner: **a fixed,
hand-written decomposition that runs in Python is not a worse version of
model-driven planning — for a task whose sub-steps never change, it's a
*strictly better* one.** It's deterministic, it's free (no extra model
call), it can't be talked out of doing step 2 by a confused model, and it's
exactly as auditable as reading the function. Model-driven planning earns
its cost only when the *set of sub-steps itself depends on the input* —
which is precisely what's missing from every agent in this app today: none
of them yet ask a question shaped like "it depends what the user asked."

---

## 3. Where this app doesn't need explicit planning (read before building §5)

Resist the pull to add a planner because it's an interesting concept to
learn (it is! — build it as an exercise, see §5.4) rather than because a
task in this app currently needs it. Concretely, run through this checklist
before wiring a planner into a real agent:

- **Does the number/order of sub-steps genuinely vary by input?**
  `import_reviewer`'s three pre-fetches never change — a planner there would
  reinvent a fixed pipeline at a real latency cost, for zero behavioral
  gain.
- **Is the task already solvable by turn-by-turn ReAct within the existing
  `agent_max_steps` budget (8, see `app/config.py`)?** `portfolio_assistant`
  answers today's multi-hop questions ("what's my drawdown vs my Monte
  Carlo forecast") by chaining 2-3 tool calls across turns — that's ReAct
  doing exactly what it's for. A planner adds value only when the *number*
  of steps a hard question needs risks exceeding the step budget, or when
  you specifically want the plan itself as an audit artifact independent of
  the tool-call log already recorded in `agent_runs.tool_calls`.
- **Would a wrong plan be worse than a wrong single step?** Plan-and-Execute
  commits early to a decomposition; if the first plan is subtly wrong, every
  step executes faithfully against the wrong plan unless you also build
  re-planning (more calls, more complexity). ReAct re-evaluates every single
  turn, which makes it more robust to the model being wrong about the
  *shape* of the task, at the cost of never having an explicit shape to
  inspect.

At this app's current scale (3 agents, tasks that are mostly "gather →
decide" or "answer a question in 1-3 hops"), the honest verdict is: **no
existing agent needs Plan-and-Execute today.** The design in §5 is here so
you (a) understand the framework by building it, and (b) have it ready the
day a genuinely compound task shows up — e.g. a future agent that has to
produce a multi-part rebalancing recommendation spanning quant, analytics,
and currency exposure, where "what are the 4-5 things I need to look at, in
what order" is a real per-input question, not a fixed pipeline.

---

## 4. The latency argument, made precisely

The task's constraint is "the effect on latency must be as small as
possible" — so make the cost of each option a number, not a vibe, using
this app's own evaluation harness shape (`security_resolver/evaluation/evaluate.py`,
walked through in `FEEDBACK_LOOPS_AND_FINE_TUNING.md` §2):

| Approach | Extra model calls vs. plain ReAct | When the cost is paid |
|---|---|---|
| ReAct (status quo) | 0 | — |
| Hardcoded decomposition | 0 | — (it's Python, not a model call) |
| Plan-and-Execute | **+1**, always — one planning call before any step runs | Every single run, even easy ones |
| Plan-and-Execute with re-plan on failure | +1, sometimes +1 more | Only on a failed step |
| Tree/Graph-of-Thought (N candidate plans) | **+N**, always | Every run, multiplied |

The one number that matters for "small effect on latency": **a
planning call costs roughly the same as one ordinary agent turn** (it's one
`chat_client.chat()` round trip with no tools bound, typically *cheaper*
than a tool-bearing turn since there's no tool-schema payload and often a
short structured-JSON response). So Plan-and-Execute's tax is "+1 turn's
worth of latency on every run, unconditionally" — that's the number to beat
before turning it on for a real agent, and it's exactly what §5.4's
evaluation phase is for: don't ship it until you've measured that tax
against the accuracy/robustness it buys, the same way Tool RAG's rollout
was gated on a real measurement rather than an assumption
(`plans/tool_rag.md` §1.1).

---

## 5. Concrete design: a reusable Plan-and-Execute wrapper

Keep the same "no framework, one small module, model/provider-agnostic"
ethos as `agent_loop.py` (`backend/ai/AGENTS.md` rule 9). The planner is a
**wrapper around `run_agent`**, not a replacement for it — every execution
step is still an ordinary `run_agent` call, so nothing about the loop, the
MCP client, or the audit trail changes; only what sits *above* the loop is
new.

### 5.1 The plan itself — one structured call, no tools bound

```python
# backend/ai/common/planner.py
"""Optional Plan-and-Execute wrapper around run_agent (see
docs/PLANNING_AND_DECOMPOSITION.md). Opt-in per agent, off by default —
mirrors ai.common.tool_rag's rollout shape. A planning call binds no tools
(tools=[]), so it's cheaper than an ordinary agent turn, not more
expensive, and its output is parsed, never trusted blindly (see
_parse_plan's fallback)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ai.common.llm import ChatClient

PLANNER_SYSTEM_PROMPT = """You decompose a user's request into 2-5 short, \
concrete subgoals that can be tackled one at a time, in order. Reply with \
ONLY a JSON array of strings, e.g. ["step one", "step two"]. If the \
request is already a single simple step, reply with a one-element array."""


@dataclass(slots=True)
class Plan:
    subgoals: list[str]
    raw: str  # the model's untouched reply — kept for the audit trail (§5.3)


def _parse_plan(raw: str, fallback_goal: str) -> Plan:
    try:
        subgoals = json.loads(raw)
        if isinstance(subgoals, list) and all(isinstance(s, str) for s in subgoals) and subgoals:
            return Plan(subgoals=subgoals[:5], raw=raw)
    except json.JSONDecodeError:
        pass
    return Plan(subgoals=[fallback_goal], raw=raw)  # never block a run on a bad plan — degrade to one step


def make_plan(user_message: str, chat_client: ChatClient) -> Plan:
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    reply = chat_client.chat(messages, tools=[])
    return _parse_plan(reply["content"], fallback_goal=user_message)
```

The fallback in `_parse_plan` is the same principle as
`import_reviewer`'s fallback note (§6 of `AGENTS_AND_MCP_EXPLAINED.md`):
**a broken plan degrades to "one step: do the whole thing," never to a
crash.** This is also why the planning call binds no tools — a
tool-less call is the cheapest, most structurally constrained call you can
make the model do, which matters doubly for a small local model asked to
produce clean JSON.

### 5.2 Executing the plan — each subgoal is an ordinary `run_agent` call

```python
# still backend/ai/common/planner.py

from collections.abc import Awaitable, Callable

from ai.common.agent_loop import AgentRunResult, run_agent


async def run_plan(
    plan: Plan,
    system_prompt: str,
    tools: list[dict],
    call_tool: Callable[[str, dict], Awaitable[object]],
    chat_client: ChatClient,
    max_steps_per_subgoal: int,
) -> list[AgentRunResult]:
    """Runs each subgoal as its own bounded run_agent call, carrying the
    previous subgoal's final_message forward as context (not its tool
    calls/results — same reasoning as portfolio_assistant's session-memory
    history param: a stale tool result from a prior subgoal risks answering
    from outdated data, so only the prior conclusion gets replayed)."""
    results: list[AgentRunResult] = []
    history: list[dict] = []
    for subgoal in plan.subgoals:
        result = await run_agent(
            system_prompt=system_prompt,
            user_message=subgoal,
            tools=tools,
            call_tool=call_tool,
            chat_client=chat_client,
            max_steps=max_steps_per_subgoal,
            terminal_tools=None,
            history=history,
        )
        results.append(result)
        if result.final_message:
            history += [{"role": "user", "content": subgoal}, {"role": "assistant", "content": result.final_message}]
    return results
```

Note what's *not* new here: no new MCP client behavior, no new tool
schema, no new terminal-tool concept. `run_plan` is ~20 lines because it
delegates everything hard back to code that already exists and is already
tested.

### 5.3 Wiring it into an agent, opt-in, with a fallback

Following the exact rollout shape `TOOL_RAG_ENABLED` established
(`app/config.py`, `ai/agents/portfolio_assistant/agent.py::_select_tools`):

```python
# ai/agents/portfolio_assistant/agent.py — sketch of the opt-in path

async def _run_loop(self, history: list[dict], user_message: str) -> tuple[AgentRunResult, dict | None]:
    ceiling, tool_retrieval = await _select_tools(user_message)
    async with open_mcp_servers(ceiling) as session:
        chat_client = build_chat_client()
        tools = await session.tool_schemas()
        prompt = PROMPT_PATH.read_text(encoding="utf-8")

        if not settings.planning_enabled:
            result = await run_agent(
                system_prompt=prompt, user_message=user_message, tools=tools,
                call_tool=session.call_tool, chat_client=chat_client,
                max_steps=settings.agent_max_steps, terminal_tools=None, history=history,
            )
            return result, tool_retrieval

        plan = make_plan(user_message, chat_client)
        step_results = await run_plan(
            plan, prompt, tools, session.call_tool, chat_client,
            max_steps_per_subgoal=settings.agent_max_steps,
        )
        final = step_results[-1]
        final.plan = plan.subgoals  # see §5.4's AgentRunResult field, for the audit trail
        return final, tool_retrieval
```

If `plan.subgoals` ends up as a single element (the fallback path, or a
genuinely simple question), this reduces to exactly one `run_agent` call —
identical behavior and identical cost to planning being off, plus the one
cheap tool-less planning call. That's the ceiling on the damage a bad plan
can do: worst case, one wasted tool-less call.

### 5.4 Observability — the plan is part of the audit trail

Add `plan: list[str] = field(default_factory=list)` to `AgentRunResult`
(`ai/common/agent_loop.py`) and a matching nullable `plan: Mapped[list |
None]` JSONB column on `AgentRunORM` (`app/adapters/persistence/orm.py`) —
same shape as Tool RAG's `tool_retrieval` column, populated only when
planning ran. This means every planned run is inspectable after the fact
via the exact same `agent_runs` table every other agent already writes to
— no new table, no new repo method beyond passing the field through
`AgentRunRecord` (`app/domain/listings.py`).

---

## 6. One synergy worth knowing about, if Tool RAG is already built

If you've implemented `docs/FEEDBACK_LOOPS_AND_FINE_TUNING.md` or
`plans/tool_rag.md` already, each subgoal string in `Plan.subgoals` is a
strictly *better* retrieval query than the raw, possibly-compound original
user message — `_select_tools`/`ToolRetriever.select` could be called once
per subgoal instead of once per turn, narrowing the tool list further per
step. This isn't required to get value from either feature alone; it's
just worth remembering they compose without any new abstraction — the same
"no new abstraction needed" principle that already governs
`open_mcp_servers`'s `{module: tool_names}` dict.

---

## 7. Config additions

```python
# app/config.py, alongside the existing agent_* settings

# --- Multi-step planning (see docs/PLANNING_AND_DECOMPOSITION.md) ---
planning_enabled: bool = False       # off by default — §3's checklist decides when to flip it per agent
planning_max_subgoals: int = 5       # hard cap, mirrors make_plan's [:5] slice
```

No new provider/embedding settings are needed — planning reuses whatever
`build_chat_client()` already returns; it is not a separate model or
service.

---

## 8. Tests

- **Unit, no model:** `_parse_plan` against valid JSON, malformed JSON,
  non-list JSON, an empty list — assert the fallback-to-one-step behavior
  exactly (mirrors how `ReplaySession`-based tests already isolate the loop
  from a real model in `backend/tests/test_mcp_quant_server.py`-style
  fixtures).
- **Unit, no model:** `run_plan` against a fake `chat_client`/`call_tool`
  pair (the same `ReplaySession` pattern
  `security_resolver/evaluation/replay.py` already uses) with a
  3-subgoal plan, asserting each subgoal's `history` argument grows as
  expected and only carries `final_message`, never tool calls.
- **Evaluation, real model, before flipping `planning_enabled=True` on any
  real agent:** extend `security_resolver/evaluation/evaluate.py`'s shape
  — run the same case suite once with planning off and once with it on,
  diff `final_accuracy` against `p50_latency_s`/`p95_latency_s`. Only ship
  the flip if accuracy improves (or step-budget failures drop) by more than
  the added latency costs you — this is §4's argument, turned into a real
  measurement instead of a guess.

---

## 9. Glossary (continuing `docs/AGENTS_AND_MCP_EXPLAINED.md`'s)

| Term | What it means | Where it would live here |
|---|---|---|
| ReAct | Interleaved reason-then-act, one step at a time, no separate plan | `ai/common/agent_loop.py::run_agent` (already built) |
| Decomposition | Breaking a task into ordered sub-steps, by code or by the model | `import_reviewer`'s pre-fetch (by code) vs. §5 (by the model) |
| Plan-and-Execute | One explicit upfront plan, executed step by step | `ai/common/planner.py::make_plan` + `run_plan` (§5) |
| Subgoal | One step of a plan, executed as its own bounded sub-run | Each element of `Plan.subgoals` |
| Re-planning | Revising the plan after a step fails, instead of running it blind | Not built in §5 — noted as a possible Phase 2 in §10 |
| Tree/Graph-of-Thought | Multiple candidate plans/branches, scored, best kept (or merged) | Deliberately not built — see `GRAPH_BASED_REASONING.md` §1c |

---

## 10. Phased implementation plan

1. **Phase 1 — build `planner.py`, wire it behind `planning_enabled`, log
   only.** Ship `make_plan`/`run_plan`/the `AgentRunResult.plan` field and
   the ORM column, default `planning_enabled=False`. No agent behavior
   changes yet; this phase is purely "the plumbing exists and is unit
   tested" (§8's first two bullets).
2. **Phase 2 — evaluate on `portfolio_assistant`.** Build a small local
   eval case suite of genuinely compound chat questions (mirroring
   `security_resolver`'s `cases_local/` mechanism), run the before/after
   comparison from §8's third bullet, and only flip `planning_enabled=True`
   in `.env` if the numbers justify it.
3. **Phase 3 (optional, only if Phase 2 shows real value) — re-planning.**
   If a subgoal's `run_agent` call ends `MAX_STEPS`/`ERROR`, feed that
   failure back into one more planner call ("subgoal N failed because X —
   revise the remaining plan") instead of blindly continuing. This is the
   first point where the framework gets genuinely more complex than a
   linear wrapper, so don't build it speculatively — build it only against
   a real observed failure pattern in Phase 2's eval data.
