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

6. **Every write goes through a use-case guard.** An agent can only ever
   mutate data via `ResolveSecurityUseCase.accept()` (terminal — applies a
   listing) or `.flag_for_review()` (hands off to a human), both already
   guarded against acting on a terminal resolution or a candidate with no
   price/currency. Never add a tool that writes to `assets`/
   `asset_resolutions`/`resolution_candidates` by any other path.

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
