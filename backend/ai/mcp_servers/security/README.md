# security MCP server

Exposes the security resolver (`app/application/resolve_security.py`) as an
MCP server over stdio, so a tool-calling agent (Phase 6) can read pending
resolutions and either save a mapping or hand it to a human — without the
agent ever touching SQL, yfinance, or OpenFIGI directly.

## Run it

```
cd backend
python -m ai.mcp_servers.security
```

Requires the `ai` extra (`pip install -e ".[ai]"`) and the same environment
as the API (`DATABASE_URL`, `OPENFIGI_API_KEY`, ...) — it uses the same
`app.container` factories and `app.config.settings` as the FastAPI app, just
without FastAPI itself.

## Tools

| Tool | Effect |
|---|---|
| `list_pending_resolutions` | Read. Resolutions waiting on a decision. |
| `get_resolution` | Read. One resolution by id. |
| `resolve_isin` | Read. Dry-run the resolver for an ISIN — no DB writes. |
| `lookup_isin` | Read. Raw OpenFIGI listings for an ISIN. |
| `search_listings` | Read. Free-text market-data search. |
| `validate_listing` | Read. Check a symbol's currency/last price. |
| `add_candidate` | **Write.** Attach a candidate to a resolution. |
| `save_security_mapping` | **Write, terminal.** Apply a candidate as the asset's mapping. |
| `flag_for_review` | **Write, terminal.** Hand the resolution to a human. |

Every tool is one function in `tools/`, thin: open a session, call a
`resolution_repo`/`build_resolve_security_use_case` factory from
`app.container`, return a JSON-safe dict via `ai.common.jsonable.to_jsonable`.
No SQL, no yfinance/OpenFIGI calls, and no scoring/decision logic lives here
— see `backend/ai/AGENTS.md` for the full rule set.

## Trying it by hand

The MCP Python SDK ships a dev inspector:

```
mcp dev ai/mcp_servers/security/server.py
```
