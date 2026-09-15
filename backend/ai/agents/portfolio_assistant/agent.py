"""The portfolio_assistant agent — a conversational chat agent over the
user's own portfolio, backed by the `portfolio`/`market_data`/`analytics`
MCP servers. See plans/agentic_asset_mapping_phase7_8.md §3.9/§4. Only
reachable via `POST /api/chat/sessions/{id}/messages`, gated on
`settings.agent_enabled` — see app/api/routes/chat.py, the only caller.

Two things distinguish this agent from every other one in `ai/agents/`:

1. **Conversational, not terminal.** It never calls a "finish" tool — the
   loop ends on the model's first plain-text reply (`terminal_tools=None`,
   see ai/common/agent_loop.py). It's also read-only: none of its tools
   write anything.
2. **Session memory.** Each call replays prior turns from `chat_messages`
   (via `run_agent`'s `history` param) before the new message, so the
   conversation actually remembers what was asked before — see
   `_load_history`'s docstring for exactly what gets replayed and why."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ai.common.agent_loop import AgentRunResult, run_agent
from ai.common.llm import build_chat_client
from ai.common.mcp_client import open_mcp_servers
from app.adapters.persistence.session import SessionLocal
from app.config import settings
from app.container import chat_repo, resolution_repo
from app.domain.listings import AgentRunRecord

AGENT_NAME = "portfolio_assistant"

# The model's tool allowlist, curated to <=10 — small local models get
# noticeably worse with more (see backend/ai/AGENTS.md). Read-only tools
# only, spread across three servers, which is exactly why this agent needs
# open_mcp_servers (a single MCP server's own tool_names filter can't
# straddle three of them). Adding a new capability later — a future
# quantitative-signals server, say — is a one-line addition to this dict;
# nothing else about this agent changes.
SERVERS: dict[str, set[str] | None] = {
    "ai.mcp_servers.portfolio": {"get_portfolio_summary", "list_positions", "get_allocation", "get_value_history"},
    "ai.mcp_servers.market_data": {"get_asset"},
    "ai.mcp_servers.analytics": {"get_returns", "get_concentration", "get_currency_exposure", "get_drawdown"},
}

# How many past chat_messages rows to replay as session memory — roughly
# 10 user/assistant turn-pairs. Older turns aren't lost (GET
# /api/chat/sessions/{id} still returns the full transcript), just not
# replayed into the model, to keep prompt size bounded as a conversation
# grows long.
MAX_HISTORY_MESSAGES = 20

PROMPT_PATH = Path(__file__).with_name("prompt.md")


def _select_tools(user_message: str) -> dict[str, set[str] | None]:
    """Which MCP servers/tools this turn gets — today, always the fixed
    allowlist above (`user_message` is unused, kept as a parameter on
    purpose). This is the extension point for a future "tool RAG" step:
    once there are more tools/servers than fit in one prompt, swap this
    for a function that embeds `user_message` against every available
    tool's description and returns just the top-K most relevant ones per
    server — nothing else in this agent, or in open_mcp_servers/run_agent,
    would need to change."""
    return SERVERS


def _auto_title(user_message: str) -> str:
    """A session's title, set once from its first message (same idea as
    ChatGPT/Claude's auto-named threads) — the frontend's history sidebar
    shows this."""
    first_line = user_message.strip().splitlines()[0] if user_message.strip() else "New chat"
    return first_line[:60] + ("…" if len(first_line) > 60 else "")


def _fallback_reply(result: AgentRunResult) -> str:
    if result.status == "TIMEOUT":
        return "Sorry, that took too long to answer — try a narrower question."
    if result.status == "MAX_STEPS":
        return "Sorry, I couldn't get to a clear answer within my step budget — try rephrasing."
    return "Sorry, something went wrong answering that. Please try again."


class PortfolioAssistantAgent:
    """Sync entry point for a request handler — see
    app/api/routes/chat.py::send_message, the only caller."""

    def send_message(self, session_id: int, user_message: str) -> AgentRunResult:
        return asyncio.run(self._send_message(session_id, user_message))

    async def _send_message(self, session_id: int, user_message: str) -> AgentRunResult:
        history = self._load_history(session_id)
        try:
            result = await asyncio.wait_for(
                self._run_loop(history, user_message), timeout=settings.agent_timeout_seconds
            )
        except TimeoutError:
            result = AgentRunResult(status="TIMEOUT", steps=0, error="agent run exceeded AGENT_TIMEOUT_SECONDS")
        except Exception as exc:  # noqa: BLE001 — deliberate: the caller (a chat request) must
            # never crash because the model or an MCP subprocess misbehaved; the user still gets
            # a reply (a fallback message, persisted like any other) — see security_resolver's
            # identical reasoning for never letting this propagate.
            result = AgentRunResult(status="ERROR", steps=0, error=str(exc))
        self._persist_turn(session_id, user_message, result, is_first_turn=not history)
        self._record_run(result)
        return result

    async def _run_loop(self, history: list[dict], user_message: str) -> AgentRunResult:
        async with open_mcp_servers(_select_tools(user_message)) as session:
            return await run_agent(
                system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
                user_message=user_message,
                tools=await session.tool_schemas(),
                call_tool=session.call_tool,
                chat_client=build_chat_client(),
                max_steps=settings.agent_max_steps,
                terminal_tools=None,
                history=history,
            )

    def _load_history(self, session_id: int) -> list[dict]:
        """Only each past turn's final text (never the tool calls/results
        that produced it) — see ai.common.agent_loop.run_agent's `history`
        param docstring for why: replaying a stale tool result risks
        answering from outdated portfolio data. The model re-calls a tool
        if it needs a current number."""
        db = SessionLocal()
        try:
            messages = chat_repo(db).list_messages(session_id, limit=MAX_HISTORY_MESSAGES)
            return [{"role": m.role, "content": m.content} for m in messages]
        finally:
            db.close()

    def _persist_turn(self, session_id: int, user_message: str, result: AgentRunResult, is_first_turn: bool) -> None:
        db = SessionLocal()
        try:
            repo = chat_repo(db)
            repo.add_message(session_id, "user", user_message)
            reply_text = result.final_message if result.status == "REPLIED" and result.final_message else _fallback_reply(result)
            repo.add_message(session_id, "assistant", reply_text, tool_calls=result.tool_calls or None)
            if is_first_turn:
                repo.rename_session(session_id, _auto_title(user_message))
            repo.touch_session(session_id)
            db.commit()
        finally:
            db.close()

    def _record_run(self, result: AgentRunResult) -> None:
        """Audit trail, same table every other agent uses
        (`resolution_id=None` — a chat turn isn't tied to any resolution,
        the same way import_reviewer's runs aren't)."""
        db = SessionLocal()
        try:
            resolution_repo(db).add_agent_run(
                AgentRunRecord(
                    resolution_id=None,
                    agent=AGENT_NAME,
                    model=settings.agent_model,
                    status=result.status,
                    steps=result.steps,
                    tool_calls=result.tool_calls,
                    final_message=result.final_message,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    duration_ms=result.duration_ms,
                    error=result.error,
                )
            )
            db.commit()
        finally:
            db.close()
