# backend/ai — rules

This package is the LLM/agent layer built on top of the (non-LLM) security
resolver in `app/`. See `plans/agentic_asset_mapping.md` for the full
design; this file is the enforceable rule set for anything added under here.

1. **Dependency direction is one-way.** `ai/` may import from `app/` freely
   (e.g. every MCP tool calls an `app.container` factory). `app/` must
   never import from `ai/`, with one exception: `app/container.py` — the
   composition root — may import from `ai/` to expose a `build_*` factory
   for a route to call (e.g. `build_security_resolver_agent`), the same way
   it wires any other adapter. Nothing outside `container.py` (no route, no
   use case, no other adapter) may import `ai/` directly. If you find
   yourself adding an `ai` import anywhere else under `app/`, stop:
   something is inverted — add a factory to `container.py` instead.

2. **MCP tools are thin.** Every tool function in `mcp_servers/*/tools/`
   does exactly this: open a DB session, call one `app.container` factory
   or one method on the use case/repo it returns, commit if it wrote
   anything, convert the result with `ai.common.jsonable.to_jsonable`,
   return. No SQL, no direct yfinance/OpenFIGI calls, and no scoring or
   decision logic belongs in `ai/` — all of that already exists in
   `app/domain` and `app/application` and must stay there.

3. **One folder per MCP server, one file per tool.** A new MCP server gets
   its own directory under `mcp_servers/` with `__main__.py`, `server.py`,
   and a `tools/` package. Each tool is its own module — never bundle
   several tools into one file.

4. **stdio servers never write to stdout.** The stdio transport *is*
   JSON-RPC — anything else written to stdout corrupts the protocol stream
   and the agent silently stops working. Logging must go to stderr only
   (see `logging.basicConfig(..., stream=sys.stderr)` in every
   `mcp_servers/*/server.py`); never use `print()` anywhere under `ai/`.

5. **Pass the full environment to MCP subprocesses.** The MCP SDK's stdio
   client only forwards a minimal environment to a spawned server process
   by default. Any code that launches one of these servers as a subprocess
   (the agent loop, Phase 6) must pass `env=dict(os.environ)` explicitly, or
   the server won't see `DATABASE_URL`/`OPENFIGI_API_KEY`/etc.

6. **Every write goes through a guard — a use case, or a repo method that
   is itself the whole guard.** An agent can only ever mutate
   `assets`/`asset_resolutions`/`resolution_candidates` via
   `ResolveSecurityUseCase.accept()` (terminal — applies a listing) or
   `.flag_for_review()` (hands off to a human), both already guarded
   against acting on a terminal resolution or a candidate with no
   price/currency — never add a tool that writes those tables by any other
   path. The one other write path an MCP tool may use is
   `NoteRepo.add()` (`ai/mcp_servers/notes/tools/save_note.py`, Phase 8c of
   plans/agentic_asset_mapping_phase7_8.md): a note is append-only with no
   state machine to violate, so the repo method itself — not a use case —
   is the guard. Any future write path needs the same property (either a
   use-case guard, or a repo method simple enough that it can't be misused)
   before a tool is allowed to call it.

7. **Every agent run is recorded.** `ResolutionRepo.add_agent_run` and the
   `agent_runs` table already exist for this. The agent loop (Phase 6) must
   call it once per run, success or failure — this is the only audit trail
   for what an agent decided and why.

8. **Evaluation cases use public data only.** The Phase 6 evaluation
   harness must not depend on this deployment's real holdings — use ISINs
   for widely-known, publicly-listed securities so the eval suite is safe
   to run and share.

9. **No agent framework.** The agent loop (Phase 6) is a small hand-rolled
   `while` loop around one `chat()` call and the MCP client — not
   LangChain/LlamaIndex/similar. Keep it legible.

10. **An agent may open more than one MCP server at once** via
    `ai/common/mcp_client.py::open_mcp_servers` (Phase 8, first used by
    `import_reviewer` and `portfolio_assistant`) when its job spans more
    than one bounded context — one dict, `{module: tool_names}`, no new
    abstraction needed to add a future server to an existing agent's
    capabilities. `tool_names=set()` (not `None`) exposes nothing from
    that server to the model while still letting the agent's own pre-fetch
    code call any of its tools directly — see
    `ai/agents/import_reviewer/agent.py`'s `SERVERS` for the pattern.
    Tool names must stay globally unique across every server one agent
    opens; `open_mcp_servers` raises at entry on a collision rather than
    silently picking one.

11. **Give a request-handler-facing agent an async entry point if its
    caller might already be `async def`.** `asyncio.run()` (the sync
    `run()` pattern every agent started with) raises "cannot be called
    from a running event loop" if called from code that's already on one
    — real bug, not hypothetical: `import_reviewer`'s first live run hit
    exactly this, because `commit_import` (`api/routes/imports.py`) is
    `async def` (it awaits `UploadFile.read()`). The fix is a second
    entry point, `async def arun()`, that the async caller awaits directly
    instead of going through `run()` — see
    `ai/agents/import_reviewer/agent.py`. A route that's plain `def`
    (FastAPI thread-pools it) has no such hazard — `resolutions.py` and
    `chat.py`'s routes call `run()`/`send_message()` (sync) with no issue.

## MCP SDK gotcha — a tool's return type shapes whether `call_tool` sees it

**A tool function annotated to return a bare `dict`** (not `dict | None`,
not a `list[dict]`, not a `TypedDict`/named-field schema) **can come back
as `None` from `McpToolSession.call_tool`, even though the tool returned
real data.** On the pinned `mcp` SDK version, `result.structured_content`
is sometimes left `None` for that specific annotation shape — this bit
`get_portfolio_summary`/`get_data_freshness` in Phase 8, and was
misdiagnosed as a `qwen3:8b` quality problem in manual testing before the
real cause was found (a "no data" chat reply was the model accurately
describing a `null` it was handed, not the model being unreliable).
`call_tool` (`ai/common/mcp_client.py`) now falls back to parsing the
tool's raw text content as JSON whenever `structured_content` is `None`,
so this is fixed for every tool going forward — no per-tool annotation
change needed, and no test that mocks the MCP layer would have caught it
either way (only a real stdio round trip exercises the SDK's own
structured-output behavior; see `test_mcp_client_multi.py`'s regression
test). Worth knowing if you're debugging a tool that "returns nothing" —
check whether it's actually `None`, not just an empty result.

## Security resolver — the one thing NOT to do

`app/main.py`, `app/api/routes/imports.py`, and `app/api/routes/sync.py`
each carry a comment explaining a real incident: wiring the resolver into
FastAPI's startup `lifespan` or into a route's `BackgroundTasks` caused
every `pytest` run to make real OpenFIGI/Yahoo network calls and mutate
real database rows, because Starlette's `TestClient` runs `lifespan` on
every `with TestClient(app) as client:` block and runs `BackgroundTasks`
*synchronously inside the request*. This actually happened once in this
project's history and silently remapped a real holding. See
`backend/AGENTS.md`'s "Security resolver" section for the full account.

That specific failure mode was about FastAPI routes and app startup, not
about `ai/` — an MCP tool calling `resolve_isin`/`save_security_mapping`
directly is fine, and is exactly what Phase 6's agent loop will do. But the
underlying principle generalizes: **never let a test's TestClient exercise
a code path that can trigger real resolution** (real OpenFIGI/Yahoo calls,
real writes) as a side effect of something that looks unrelated — a
startup hook, a background task, an MCP server auto-started by a fixture.
Keep resolution invocation explicit and visible at every call site.
