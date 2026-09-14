# security_resolver agent

A tool-calling LLM that finishes a security resolution the deterministic
resolver (`app/application/resolve_security.py`) couldn't decide on its
own — a resolution left in `NEEDS_AGENT`. See
`plans/agentic_asset_mapping.md` Phase 6. The model backend is pluggable —
[Ollama](https://ollama.com) locally, or any OpenAI-compatible hosted API
(Groq, OpenAI itself, Together, Fireworks, ...) — see Configuration below.

## How it runs

`SecurityResolverAgent.run(resolution_id)` (sync, blocking):

1. Starts the `security` MCP server (`ai/mcp_servers/security/`) as a stdio
   subprocess, restricted to the 6 tools in `agent.py::AGENT_TOOLS`.
2. Fetches the resolution once via `get_resolution` and renders it into a
   compact text block (`render_user_message`) — put in the first user
   message so a small model can't skip reading it.
3. Runs `ai.common.agent_loop.run_agent` with `prompt.md` as the system
   prompt, until the model calls `save_security_mapping` or
   `flag_for_review` (or the loop gives up: `MAX_STEPS`/`ERROR`).
4. The whole run is wrapped in `asyncio.wait_for(..., AGENT_TIMEOUT_SECONDS)`.
5. **Deterministic fallback:** if the run didn't end in `SAVED`/`FLAGGED`
   (timeout, error, max steps, or a model that got nudged twice), the agent
   calls `flag_for_review` itself — a resolution never stays stuck in
   `NEEDS_AGENT` because the model misbehaved.
6. Every run — success or failure — is recorded via
   `ResolutionRepo.add_agent_run` (the `agent_runs` table) for audit and
   for building evaluation cases from real failures (see `evaluation/`).

Called from exactly one place: `POST /api/resolutions/{id}/agent`
(`app/api/routes/resolutions.py`), an explicit user action, gated on
`AGENT_ENABLED=true`. Never scheduled, never wired into `BackgroundTasks` —
see `backend/AGENTS.md`'s "Security resolver" section for why that
distinction matters in this codebase.

## Configuration

`AGENT_ENABLED`, `AGENT_PROVIDER`, `AGENT_BASE_URL`, `AGENT_API_KEY`,
`AGENT_MODEL`, `AGENT_MAX_STEPS`, `AGENT_TIMEOUT_SECONDS`, `AGENT_NUM_CTX`
— see `backend/.env.example` for the full explanation of each.

`AGENT_PROVIDER` picks the model backend (`ai.common.llm.build_chat_client`):

- `ollama` (default) — Ollama's native API at `AGENT_BASE_URL`
  (`http://localhost:11434` locally; `http://host.docker.internal:11434`
  from `docker compose`, since Ollama itself isn't part of the compose
  stack). Requires `AGENT_MODEL` pulled and tool-capable (its Ollama
  library page must list **tools** under capabilities — not every model
  does; `qwen3:8b`/`qwen3:14b` do).
- `openai` — any OpenAI-compatible `/chat/completions` endpoint.
  `AGENT_BASE_URL` is the full API root including `/v1` (e.g.
  `https://api.groq.com/openai/v1` for Groq), `AGENT_API_KEY` the bearer
  token, `AGENT_MODEL` that provider's model name.

Switching models or providers — `qwen3:8b` → `qwen3:14b`, or local Ollama
→ a Groq-hosted model — is editing these four values, never a code change.

## Files

- `agent.py` — `SecurityResolverAgent`, `render_user_message`, the tool
  allowlist and terminal-tool status mapping.
- `prompt.md` — the system prompt. Kept as a plain file (not a Python
  string) so evaluation can diff/version it independently of code changes.
- `evaluation/` — offline, deterministic evaluation harness for comparing
  models before picking `AGENT_MODEL` — see `evaluation/README.md`.

## Shared building blocks it depends on (`ai/common/`)

- `llm.py::build_chat_client` — the one factory that picks a model
  backend (`OllamaChatClient` or `OpenAiCompatibleChatClient`) from
  settings; `agent_loop.run_agent` only ever sees the resulting
  `ChatClient`'s `.chat(messages, tools)`, never a provider detail.
- `mcp_client.py::open_mcp_server`/`McpToolSession` — starts an MCP server
  subprocess and adapts its tools/results to what `agent_loop` expects.
- `agent_loop.py::run_agent` — the one tool-calling loop every agent under
  `ai/agents/` uses; no agent framework (see `backend/ai/AGENTS.md` rule 9).
