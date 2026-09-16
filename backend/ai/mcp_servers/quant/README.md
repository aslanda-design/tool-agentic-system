# quant MCP server

Exposes the Quant Lab (`app/application/run_quant_simulation.py`,
`app/domain/quant/`) as an MCP server over stdio — see `plans/quant_lab.md`.
**Read-only/advisory only**: an agent can list the registered models, get a
deterministic recommendation for an asset, and explain a run the user
already made on the Quant Lab page. It cannot trigger a simulation itself —
`POST /api/quant/simulate` stays a UI-only action (see `plans/quant_lab.md`
section 0.2 for why).

## Run it

```
cd backend
python -m ai.mcp_servers.quant
```

Requires the `ai` and `quant` extras (`pip install -e ".[ai,quant]"`) and
the same environment as the API (`DATABASE_URL`, ...) — it uses the same
`app.container` factories as the FastAPI app.

## Tools

| Tool | Effect |
|---|---|
| `list_models` | Read. Every registered quant model + its param specs. |
| `recommend_model` | Read. Deterministic (non-LLM) model recommendation for an asset. |
| `explain_run` | Read. A stored run's recipe + summary — never the raw simulated paths. |

Every tool is one function in `tools/`, thin: open a session, call
`build_run_quant_simulation_use_case` from `app.container`, return a
JSON-safe result via `ai.common.jsonable.to_jsonable`. No math lives here —
that's `app/domain/quant/` — and no write path exists on this server at
all, so none of `backend/ai/AGENTS.md` rule 6's write-guard requirements
apply to it.

## Using it from `portfolio_assistant`

`ai/agents/portfolio_assistant/agent.py`'s `SERVERS` dict includes this
server (all three tools exposed to the model) alongside `portfolio`,
`market_data`, and `analytics` — so a user can ask things like "what model
fits my VWCE position?" or "what did that Monte Carlo run on my Nvidia
position actually show?" in the existing chat page, no new UI needed.

## Trying it by hand

`.mcp.json` (repo root, no secrets — the server reads `backend/.env`):

```json
{ "mcpServers": { "investment-quant": {
    "command": "backend/.venv/Scripts/python", "args": ["-m", "ai.mcp_servers.quant"], "cwd": "backend" } } }
```
