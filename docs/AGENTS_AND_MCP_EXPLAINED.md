# How agents, tools, and MCP servers work in this app

This document explains, point by point, how the AI/agent layer of this
repository (`backend/ai/`) works — both the general concept and this app's
concrete implementation. It exists because the app itself was built to learn
these mechanics, so every section pairs a fundamental explanation with the
real file that implements it.

**Verdict on completeness:** this app already *is* a working, non-trivial
example of the full stack — a hand-rolled agent loop, a pluggable
multi-provider LLM client, 7 MCP servers (30+ tools spanning read, search,
guarded-write, and predictive-model categories), 3 agents that each
demonstrate a different pattern (single-shot terminal, multi-server
pre-fetch, conversational-with-memory), an audit trail, and an offline
evaluation harness with real metrics. You do not need to build more scaffolding
before this is a good teaching example — the rest of this file *is* the
walkthrough. A short "further exercises" section at the end lists optional
extensions if you want to keep exploring after this makes sense.

---

## 1. The core idea: what "an agent" actually is

Strip away the marketing, and an LLM agent is one loop:

```
┌─────────────────────────────────────────────────────────────┐
│  1. Send the model: system prompt + conversation so far      │
│                    + a list of tools it's allowed to call    │
│                                                                │
│  2. Model replies with either:                                │
│       (a) plain text            → done, or                    │
│       (b) one or more tool calls → go to 3                    │
│                                                                │
│  3. Run each tool call for real (HTTP, DB, subprocess, math)  │
│     Append the tool's result to the conversation as a         │
│     "tool" message                                             │
│                                                                │
│  4. Go back to 1 — model sees the new tool result and decides │
│     what to do next                                            │
└─────────────────────────────────────────────────────────────┘
```

That's it. Everything else — MCP, frameworks like LangChain, "multi-agent
systems" — is packaging around this loop: how tools are *described* to the
model, how tool calls are *transported* and *executed*, and how many of
these loops are wired together.

This app implements that loop directly, no framework, in
[`backend/ai/common/agent_loop.py`](../backend/ai/common/agent_loop.py)
(`run_agent`). It's ~120 lines. Reading it top to bottom is the fastest way
to actually understand agents — every "agent framework" you'll ever
encounter is doing a fancier version of this same `for` loop.

```python
for step in range(1, max_steps + 1):
    reply = chat_client.chat(messages, tools)          # 1 + 2
    messages.append({"role": "assistant", ...})

    if not reply["tool_calls"]:
        return finish("REPLIED", reply["content"])      # done (text-only reply)

    for call in reply["tool_calls"]:                    # 3
        output = await call_tool(call["name"], call["arguments"])
        messages.append({"role": "tool", "content": json.dumps(output)})
        if terminal_tools and call["name"] in terminal_tools:
            return finish(terminal_tools[call["name"]])  # done (terminal tool)
                                                          # 4: loop continues otherwise
```

Two design decisions worth internalizing because they generalize far beyond
this app:

- **Two ways to end a run.** A *task* agent (do X, then stop) ends when it
  calls a specific "terminal" tool — e.g. `save_security_mapping` or
  `flag_for_review`. A *conversational* agent ends on the model's first
  plain-text reply, because for a chat agent, talking back **is** the
  successful outcome, not a failure to finish. See `terminal_tools=None` vs
  a dict in the docstring of `run_agent`.
- **The loop doesn't know what an LLM is, or what a tool is.** `chat_client`
  is any object with a `.chat(messages, tools) -> dict` method; `call_tool`
  is any `async (name, args) -> result` function. That's why the same 120
  lines drive three completely different agents in this repo (§5) and why
  swapping models/providers is a config change, not a code change (§2).

---

## 2. Feeding the model: the LLM client abstraction

The loop needs a `chat_client`. This app supports two backends behind one
interface (`ChatClient` protocol in
[`backend/ai/common/llm.py`](../backend/ai/common/llm.py)):

```
                    ┌───────────────────────┐
  messages, tools → │  ChatClient.chat(...)  │ → {content, tool_calls,
                    └───────────┬───────────┘     prompt_tokens, ...}
                                │
                 ┌──────────────┴───────────────┐
                 ▼                               ▼
      OllamaChatClient                 OpenAiCompatibleChatClient
      (local model, e.g.               (Groq / OpenAI / Together /
       qwen3:8b via Ollama)             any /chat/completions API)
```

Why this matters for learning: every provider has a *slightly* different
wire format for the same concept (a tool call). Ollama nests
`{"function": {"name", "arguments"}}`; OpenAI-compatible APIs need a
generated `id`/`tool_call_id` pair linking each tool result back to the call
that produced it, and `arguments` as a JSON **string**, not an object. This
app's internal message shape (`{"role", "content", "tool_calls":
[{"name","arguments"}]}`) is deliberately provider-agnostic, and each client
translates in both directions (`_to_ollama_messages`, `_to_wire_messages` /
`_from_wire_response`). One of these translation bugs was a real incident —
see `backend/ai/AGENTS.md`'s note on `OllamaChatClient`'s pydantic
validation — which is a good illustration of why "just call the API" is
never quite the whole story once tool-calling is involved.

`build_chat_client()` picks a backend from `.env` (`AGENT_PROVIDER`,
`AGENT_MODEL`, `AGENT_BASE_URL`, `AGENT_API_KEY` — see
`backend/.env.example`). Nothing in `agent_loop.py` or any agent hardcodes a
model — that's the point of the abstraction.

---

## 3. Feeding the model tools: what MCP actually solves

### 3.1 The problem MCP solves

Before MCP, "give an LLM a tool" meant: write a JSON schema by hand, write
the function, write the glue code that routes a tool call to that function,
and repeat that glue for every agent that wants the same tool. If a tool
lived in a different process (a database, an external API, a sandboxed
executor), you had to build your own RPC for it too.

**MCP (Model Context Protocol)** standardizes this: a *server* process
exposes a set of tools (name, description, JSON-schema parameters, and a
`call` function) over a small JSON-RPC protocol. Any client — this app's
agent loop, Claude Desktop, another agent — can connect, ask
"what tools do you have?" (`list_tools`), and invoke one (`call_tool`)
without knowing anything about how the server implements it.

```
┌──────────────┐   stdio (JSON-RPC over stdin/stdout)   ┌───────────────────┐
│  Agent loop   │ ─────────────────────────────────────▶ │  MCP server        │
│ (this process)│ ◀───────────────────────────────────── │ (subprocess)       │
└──────────────┘        list_tools() / call_tool()        └───────────────────┘
                                                              │
                                                              ▼
                                                     app/container.py factory
                                                     → real DB / API / math
```

The server is just a regular Python process. This app spawns each one as a
subprocess over **stdio** transport (as opposed to MCP's other transport,
HTTP+SSE, used when the server lives on a different machine) — see
`_server_params` in
[`backend/ai/common/mcp_client.py`](../backend/ai/common/mcp_client.py):

```python
def _server_params(module: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", module], cwd=str(BACKEND_DIR),
        env=dict(os.environ),   # <- must forward the full env or DB/API keys vanish
    )
```

**A gotcha worth knowing generally, not just here:** stdio *is* the
transport. If a server (or a library it imports) ever calls `print()`, that
text corrupts the JSON-RPC stream and the agent silently breaks. Every
server in this repo logs to `stderr` only
(`logging.basicConfig(..., stream=sys.stderr)`) — see rule 4 in
`backend/ai/AGENTS.md`. This is the single most common "my MCP server
doesn't work and I don't know why" bug in practice.

### 3.2 One server, one bounded context, thin tools

This repo has **7 MCP servers**, one per bounded context:

| Server | Purpose | Example tools |
|---|---|---|
| `security` | resolve which market-data listing prices a broker holding | `search_listings`, `lookup_isin`, `validate_listing`, `save_security_mapping`, `flag_for_review` |
| `portfolio` | read the user's holdings | `get_portfolio_summary`, `list_positions`, `get_allocation`, `get_value_history` |
| `market_data` | prices/FX/freshness | `get_asset`, `get_price_history`, `get_fx_rate`, `get_data_freshness` |
| `analytics` | derived numbers over the portfolio | `get_returns`, `get_concentration`, `get_currency_exposure`, `get_drawdown` |
| `notes` | append-only agent-written notes | `save_note`, `list_notes` |
| `quant` | **predictive/statistical models** | `list_models`, `recommend_model`, `explain_run` |
| `market_intel` | external (non-portfolio) data — news, filings, sentiment | `get_asset_news`, `get_filing_events`, `get_asset_sentiment`, `get_macro_headlines` |

Every tool follows the same shape (rule 2 in `backend/ai/AGENTS.md`, the
project's explicit "MCP tools are thin" rule): open a DB session, call
**one** factory/method from `app.container` (the same hexagonal-architecture
composition root the REST API uses), convert the result to JSON with
`to_jsonable`, return. No SQL, no business logic, no scoring — that already
lives in `app/domain` and `app/application`. Concretely:

```python
# backend/ai/mcp_servers/security/tools/search_listings.py
def search_listings(query: str) -> list[dict]:
    """Free-text search for market-data listings by symbol or name."""
    db = SessionLocal()
    try:
        use_case = build_resolve_security_use_case(db)
        results = use_case.market_data.search(query)
        return [to_jsonable(result) for result in results]
    finally:
        db.close()
```

This is the general lesson: **an MCP tool is not where you put logic — it's
a thin adapter that exposes logic your app already has.** The docstring
(`"""Free-text search..."""` plus the `Args:`/`Returns:` block) is not
decoration — the MCP SDK turns it directly into the tool's JSON-schema
`description`, which is the *only* thing the model sees when deciding
whether to call this tool. A vague docstring produces a model that misuses
the tool; this is prompt engineering, just distributed across your codebase
instead of one big prompt.

### 3.3 Registering tools on a server

A server (`server.py`) does almost nothing besides registration:

```python
# backend/ai/mcp_servers/quant/server.py
mcp = MCPServer("quant")
mcp.tool()(list_models)
mcp.tool()(recommend_model)
mcp.tool()(explain_run)
```

`mcp.tool()` inspects the function's type hints and docstring to build the
JSON-schema the client will see via `list_tools()`. This is the MCP SDK
doing automatically what you'd otherwise hand-write as:

```json
{
  "type": "function",
  "function": {
    "name": "recommend_model",
    "description": "Deterministic, non-LLM model recommendation for an asset...",
    "parameters": {"type": "object", "properties": {"asset_id": {"type": "integer"}}, "required": ["asset_id"]}
  }
}
```

### 3.4 Predictive models as tools — the `quant` server

This directly answers "how do I let an agent use a predictive model": you
don't hand the model raw math — you wrap a **use case** in a tool, exactly
like every other tool. The quant server's tools call into
`app/domain/quant/`, a small model-plugin registry
(`RunQuantSimulationUseCase`, `MODEL_REGISTRY`) holding several real
calibrated models:

- `black_scholes_gbm.py` — closed-form Geometric Brownian Motion
- `heston.py` — stochastic-volatility, Euler-discretized
- `hawkes_jump_diffusion.py` — GBM + self-exciting jump clustering, MLE-fit
- `linear_regression.py`, `time_series_ar.py` — simple statistical baselines

Adding a new model is one file implementing the `QuantModel` protocol plus
one import line in `registry.py` — nothing in the API route, MCP server, or
frontend needs to change (`backend/app/domain/quant/registry.py`'s
docstring). This registry pattern is worth generalizing: **it's the same
shape as adding a new tool to a server** — a fixed interface, an explicit
registration point, zero central "if model == X" branching.

The agent-facing side is intentionally narrow and read-only/advisory
(`recommend_model` returns a **deterministic, non-LLM** ranking — Ljung-Box
for volatility clustering, trend strength, excess kurtosis — the model never
*is* the predictive model; it just decides whether to mention the
recommendation to the user). `explain_run` lets the chat agent describe a
simulation the user already ran on the `/quant` page; the agent can never
*trigger* a simulation itself (see `plans/quant_lab.md` §0.2) — a
deliberate scope boundary, not an oversight. This is a second general
lesson: **giving an agent access to a powerful/expensive/stateful capability
doesn't mean giving it write access to trigger it** — read-only wrapper
tools are a legitimate, common pattern for "advisory" agents.

### 3.5 "Search" tools, and where MCP meets an external API

`lookup_isin` and `search_listings`
(`backend/ai/mcp_servers/security/tools/`) are this app's search tools —
they call **OpenFIGI** (external API) and the local Postgres `pg_trgm`
search index respectively. The pattern is identical to any tool: a thin
wrapper over a `MarketDataPort` adapter that already exists for the
non-agent codepath. If you wanted to add a genuine web-search tool (e.g.
Brave Search, Tavily) for a future research-style agent, it would be a new
file in a new `mcp_servers/web_search/tools/` following exactly this
recipe — call the API, `to_jsonable` the result, return. Nothing about the
agent loop or MCP client would change.

### 3.6 Reading tools from Python — the client side

The client mirrors the server: `McpToolSession` in
`ai/common/mcp_client.py` wraps one connected session and exposes exactly
what the agent loop needs:

```python
async def tool_schemas(self) -> list[dict]:      # -> what the model sees
async def call_tool(self, name, arguments) -> Any:  # -> what actually runs it
```

`call_tool`'s docstring documents a real, non-obvious SDK bug: a tool
annotated to return a bare `dict` can come back as `None` from
`call_tool` even though the tool returned real data, because
`structured_content` is sometimes left `None` for that exact annotation
shape on the pinned SDK version. This was first *misdiagnosed as the LLM
being unreliable* — the chat agent was accurately describing a `null` it
had been handed. The fix (parse the tool's raw text content as JSON when
structured content is missing) is now baked into `call_tool` so no
individual tool needs a workaround. **This is a genuinely useful lesson for
anyone learning MCP: when a tool "returns nothing," check the transport
layer before assuming the model is wrong.**

### 3.7 Opening more than one server at once

An agent whose job spans more than one bounded context (e.g.
`portfolio_assistant` needs `portfolio` + `market_data` + `analytics` +
`quant`) uses `open_mcp_servers({module: tool_names})` — one dict, no new
abstraction:

```python
SERVERS: dict[str, set[str] | None] = {
    "ai.mcp_servers.portfolio": {"get_portfolio_summary", "list_positions", ...},
    "ai.mcp_servers.market_data": {"get_asset"},
    "ai.mcp_servers.analytics": {"get_returns", "get_concentration", ...},
    "ai.mcp_servers.quant": {"list_models", "recommend_model", "explain_run"},
}
```

`MultiServerToolSession` merges every server's `tool_schemas()` into one
list for the model, and dispatches `call_tool(name, args)` to whichever
server actually owns that name — raising immediately at startup if two
servers expose a tool with the same name, rather than silently picking one.

```
                  ┌─────────────────────────────────────────┐
                  │           MultiServerToolSession          │
                  │  tool_schemas() → merges all servers'     │
                  │  call_tool(name, args) → dispatch by name │
                  └───────┬───────────┬───────────┬──────────┘
                          │           │           │
              ┌───────────▼──┐ ┌──────▼─────┐ ┌───▼────────┐
              │  portfolio    │ │ market_data│ │  quant      │
              │  (subprocess) │ │(subprocess)│ │(subprocess) │
              └───────────────┘ └────────────┘ └─────────────┘
```

A subtle but important trick: `tool_names=set()` (not `None`) means "expose
*none* of this server's tools to the model" while still letting the
*agent's own Python code* call any of its tools directly. `import_reviewer`
uses this to pre-fetch context (mispriced trades, stale prices, pending
resolutions) from three servers *before* the model ever sees a prompt — the
model's only real job is deciding whether that pre-fetched context deserves
a note. This is a third general lesson: **not every tool call has to come
from the model.** Deterministic pre-fetching is cheaper, faster, and more
reliable than hoping a small model calls the right three read tools in the
right order before it can reason about anything.

---

## 4. Small-model-specific lessons baked into this codebase

This app was tuned against small local models (`qwen3:8b` via Ollama), which
surfaces lessons that don't show up if you only ever test against a frontier
hosted model:

- **Tool budgets matter.** `portfolio_assistant`'s allowlist is deliberately
  capped near 10 tools total, spread across servers — "small local models
  get noticeably worse with more" (see the `SERVERS` comment in
  `agent.py`). This is why `_select_tools` exists as a named extension
  point (currently a stub returning the fixed list) for a future "tool
  RAG" step — embed the user's message against tool descriptions, expose
  only the top-K per turn — without touching the loop or MCP client.
- **Don't make the model fetch what you can hand it.** Every agent's first
  user message is a *rendered, pre-fetched* summary of exactly what it
  needs to decide (`render_user_message` in `security_resolver` and
  `import_reviewer`), not "go look this up." A small model that has to
  chain 3 tool calls before it can start reasoning fails more often than
  one hazard away from the answer.
- **Terminal tools need a nudge, not a hang.** If the model replies with
  plain text instead of calling a required terminal tool, `run_agent` sends
  one nudge ("You must finish by calling one of: ...") before giving up
  with `ERROR` — a small model genuinely does sometimes just... stop.
- **Every failure mode has a deterministic fallback.** `security_resolver`
  falls back to `flag_for_review` (hand it to a human) on any non-terminal
  outcome; `import_reviewer` writes a note saying the review itself failed.
  **An agent's failure path is part of the design, not an afterthought** —
  see §6.

---

## 5. Three agents, three shapes

```
security_resolver         import_reviewer            portfolio_assistant
───────────────────      ───────────────────        ───────────────────
1 MCP server              4 MCP servers               4 MCP servers
(open_mcp_server)         (open_mcp_servers,          (open_mcp_servers)
                           3 opened tool_names=set()
                           just for pre-fetch)

terminal_tools=           terminal_tools=              terminal_tools=None
 {save_security_mapping    {save_note: SAVED}          (ends on first
  : SAVED,                                              plain-text reply)
  flag_for_review:
  FLAGGED}

no history                no history                   history=last 20
                                                         chat_messages rows
                                                         (session memory)

writes: assets/           writes: notes                writes: nothing
resolutions (guarded                                    (read-only tools
via a use case)                                          only)
```

- **`security_resolver`** — a single-server, single-decision task agent.
  Fetches one resolution's candidates up front, then must end by calling
  either `save_security_mapping` (accept) or `flag_for_review` (defer to a
  human) — both guarded by `ResolveSecurityUseCase`, the same guard the
  non-agent code path uses (rule 6, `backend/ai/AGENTS.md`).
- **`import_reviewer`** — a multi-server, pre-fetch-then-decide agent. Its
  only model-facing tool is `save_note`; three other servers exist purely
  so its own Python code can gather context before the model ever runs.
  Ships both a sync `run()` and an async `arun()` entry point, because its
  caller (`commit_import`) is itself `async def` — a real
  `asyncio.run()`-inside-a-running-loop crash from a live run is documented
  in rule 11 of `backend/ai/AGENTS.md`.
- **`portfolio_assistant`** — a conversational, read-only, memory-carrying
  chat agent behind `/api/chat/sessions/{id}/messages` and a full two-pane
  frontend (`frontend/src/features/assistant/AssistantPage.tsx`). It
  replays prior turns' **final text only** (never their tool calls/results)
  as history — replaying a stale tool result risks answering from outdated
  portfolio data, so the model just re-calls the tool if it needs a current
  number. This is the cheapest correct version of "chat memory": no vector
  store, no summarization step, just "what did we conclude last time."

Every agent, regardless of shape, writes one row to `agent_runs`
(`ResolutionRepo.add_agent_run` — model, status, steps, every tool call,
token counts, duration, error) on every run, success or failure. This is
the audit trail — the answer to "what did the agent actually do and why" —
and it's also what the evaluation harness (§7) is built to reason about.

---

## 6. Guardrails: what an agent is and isn't allowed to do

Three principles run through every agent in this repo, and they generalize
to any agent system you build:

1. **Writes go through the same guard the human path uses, not a shortcut.**
   `security_resolver` can only mutate `assets`/`asset_resolutions` via
   `ResolveSecurityUseCase.accept()` or `.flag_for_review()` — the exact
   methods a human reviewer's REST call also goes through. There is no
   "agent-only" write path with weaker guarantees.
2. **Read-only vs. read-write is an explicit, named boundary**, not a side
   effect of which tools happen to be wired up. `portfolio_assistant`'s
   tool list is 100% read-only by construction; the `quant` server is
   explicitly advisory (§3.4). If you're deciding whether a new agent needs
   write access, this repo's answer is: default to read-only, and add
   exactly one guarded write path when there's a concrete reason.
3. **Never let a test exercise a real side effect by accident.**
   `backend/ai/AGENTS.md`'s "Security resolver — the one thing NOT to do"
   section documents a real incident: wiring resolution into FastAPI's
   `lifespan` or `BackgroundTasks` caused `pytest` to make real
   OpenFIGI/Yahoo calls and mutate real rows, because `TestClient` runs
   both of those synchronously. The fix was a rule, not a patch: resolution
   invocation must stay explicit and visible at every call site — never
   implicit in app startup or a background task.

---

## 7. Testing and evaluating agents (not just unit-testing code)

Two different things need testing here, and conflating them is a common
mistake:

- **Does the harness work?** (deterministic, fast, no LLM) — covered by
  ordinary `pytest` tests with a `ReplaySession` fixture standing in for a
  real MCP server (e.g. `backend/tests/test_mcp_quant_server.py`). This
  catches "the loop/tool wiring is broken," not "the model is bad."
- **Is the *model* good enough at this task?** — this is genuinely
  different, and needs a real LLM in the loop. `security_resolver`'s
  offline evaluation harness
  (`backend/ai/agents/security_resolver/evaluation/evaluate.py`) runs a
  case suite (public-ISIN fixtures — rule 8, `backend/ai/AGENTS.md`)
  against a real model and reports `final_accuracy`, `safety_violations`,
  `terminated_ok`, `invalid_calls`, latency percentiles, and token usage —
  per model, so you can compare e.g. `qwen3:8b` vs `qwen3:14b` vs a
  Groq-hosted model. This surfaced a real result documented in the root
  `AGENTS.md`: `mistral:latest` scored 0/6 (not tool-tuned) — which proved
  the *fallback* worked correctly rather than indicating a harness bug —
  while `qwen3:8b` scored 5/6 with zero safety violations. **This is the
  difference between "my code runs" and "my agent is trustworthy enough to
  ship,"** and it's the piece most tutorials skip entirely.

---

## 8. The full request path, end to end

Putting every layer together for one real turn of `portfolio_assistant`:

```
 Frontend (AssistantPage.tsx)
        │  POST /api/chat/sessions/{id}/messages { message }
        ▼
 FastAPI route (app/api/routes/chat.py::send_message)
        │  PortfolioAssistantAgent.send_message(session_id, message)
        ▼
 _load_history()  →  chat_repo.list_messages(limit=20)   [session memory]
        │
        ▼
 open_mcp_servers({portfolio, market_data, analytics, quant})
        │  spawns 4 subprocesses, `python -m ai.mcp_servers.<x>`, over stdio
        ▼
 run_agent(system_prompt, message, tools, call_tool, chat_client,
           terminal_tools=None, history=...)
        │
        ├─▶ chat_client.chat(messages, tools)     [build_chat_client(): Ollama or OpenAI-compatible]
        │        └─ model replies with a tool_call, e.g. get_portfolio_summary
        ├─▶ call_tool("get_portfolio_summary", {})
        │        └─ MultiServerToolSession dispatches → portfolio server
        │                └─ tool opens a DB session, calls app.container factory,
        │                   to_jsonable(), returns
        ├─▶ chat_client.chat(... + tool result ...)
        │        └─ model replies with plain text  → loop ends, status=REPLIED
        ▼
 _persist_turn()   → chat_repo.add_message (user + assistant), rename/touch session
 _record_run()     → resolution_repo.add_agent_run (audit trail)
        ▼
 FastAPI returns the reply → frontend renders it
```

Every box in that diagram is a real file you can open and read; nothing is
hidden behind a framework.

---

## 9. Glossary (fundamentals, mapped to this codebase)

| Term | What it means | Where in this repo |
|---|---|---|
| Agent loop | The send→reply→(tool or done)→repeat cycle | `ai/common/agent_loop.py::run_agent` |
| Tool | A function + JSON-schema description the model can choose to call | any file under `ai/mcp_servers/*/tools/` |
| Tool schema | The JSON description of a tool's name/args, sent to the model each turn | `McpToolSession.tool_schemas()` |
| MCP server | A process exposing tools over a standard protocol | `ai/mcp_servers/<name>/server.py` |
| Transport | How client and server exchange JSON-RPC messages (stdio here; HTTP+SSE is the other MCP option) | `mcp.client.stdio.stdio_client` in `mcp_client.py` |
| Terminal tool | A tool whose successful call ends the agent run with a given status | `TERMINAL_TOOLS` dicts in each task agent |
| System prompt | Fixed instructions for the model, same every run | `ai/agents/*/prompt.md` |
| Pre-fetch | Fetching context via direct tool calls before the model ever runs, instead of hoping it calls the right tools itself | `import_reviewer._run_loop`, `tool_names=set()` pattern |
| Session memory | Replaying prior turns so a conversation remembers earlier context | `portfolio_assistant._load_history` |
| Audit trail | A persisted record of every agent decision, for later inspection | `agent_runs` table / `add_agent_run` |
| Offline evaluation | Running an agent against a fixed case suite with a real LLM to measure task performance, not just code correctness | `security_resolver/evaluation/evaluate.py` |
| Model registry | A pluggable set of interchangeable implementations behind one interface, added by registration not branching | `app/domain/quant/registry.py` |

---

## 10. Further exercises (optional — the app doesn't need these to be a good example)

If you want to keep extending this as a learning project, in roughly
increasing order of how much new ground they cover versus what's already
here:

1. **Implement `_select_tools`'s "tool RAG" hook for real** (embed the
   user's message against tool descriptions, expose only the top-K) — the
   extension point already exists and is named in
   `portfolio_assistant/agent.py`.
2. **Add a genuine external web-search tool** (Tavily/Brave) as a new MCP
   server, following the `search_listings` recipe in §3.5 — gives you a
   second example of "thin tool wraps an external API" beyond OpenFIGI.
3. **Try the HTTP+SSE MCP transport** instead of stdio for one server, to
   see the other half of the protocol (this matters once a tool server
   needs to live on a different machine than the agent).
4. **A supervisor/orchestrator agent** that decides *which* of the existing
   three agents to invoke for a given request, instead of each being
   reached by a single fixed route — this is the smallest possible
   "multi-agent system" and would sit naturally on top of what's here.
5. **Finish `rough_heston`** (`app/domain/quant/models/rough_heston.py`,
   currently untracked/in progress) and wire it into the registry — a
   concrete, self-contained exercise in the plugin pattern from §3.4.
