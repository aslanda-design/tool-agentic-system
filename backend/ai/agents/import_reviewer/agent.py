"""The import_reviewer agent — checks a fresh statement import for problems
(mispriced trades, stale prices, unmapped assets, pending resolutions) and
writes one note about what it found, using the `notes` MCP server's
`save_note` tool. See plans/agentic_asset_mapping_phase7_8.md §3.7/§4.
Only runs when `settings.agent_enabled` is true — see
app/api/routes/imports.py::commit_import, the only caller.

Single-turn in practice: every piece of context this agent needs is
fetched once, before the loop starts, and put in the first user message
(same reasoning as security_resolver's resolution pre-fetch — a small
model can't skip reading it, and it saves a step). The model's only real
job is deciding whether any of it is worth a note."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ai.common.agent_loop import AgentRunResult, run_agent
from ai.common.llm import build_chat_client
from ai.common.mcp_client import open_mcp_servers
from app.adapters.persistence.session import SessionLocal
from app.config import settings
from app.container import note_repo, resolution_repo
from app.domain.listings import AgentRunRecord

AGENT_NAME = "import_reviewer"

# Three servers opened purely for their pre-fetch tools (tool_names=set() —
# nothing from them is exposed to the model, see ai/common/mcp_client.py's
# open_mcp_servers docstring); `notes` is the model's one real tool. Adding
# a future data source here (e.g. a quantitative-signals server) is a
# one-line addition to this dict.
SERVERS = {
    "ai.mcp_servers.analytics": set(),
    "ai.mcp_servers.market_data": set(),
    "ai.mcp_servers.security": set(),
    "ai.mcp_servers.notes": {"save_note"},
}
TERMINAL_TOOLS = {"save_note": "SAVED"}

PROMPT_PATH = Path(__file__).with_name("prompt.md")


def render_user_message(account_id: int, mispriced: list[dict], freshness: dict, pending: list[dict]) -> str:
    """Compact text block describing everything this run found — put in
    the first user message rather than fetched via a tool call, so a small
    model can't skip reading it (same reasoning as security_resolver's
    render_user_message)."""
    lines = [f"Reviewing account {account_id} after an import."]

    if mispriced:
        lines.append("Trades priced suspiciously far from that day's market close:")
        for m in mispriced:
            lines.append(
                f"  - {m['symbol']}: executed at {m['executed_price']}, market closed at "
                f"{m['market_close']} that day ({m['pct_diff'] * 100:+.1f}%)"
            )
    else:
        lines.append("No mispriced trades found.")

    stale = freshness.get("stale_positions") or []
    if stale:
        lines.append("Held positions with no recent price:")
        for p in stale:
            lines.append(f"  - {p['symbol']} (last priced {p['last_price_date'] or 'never'})")

    unmapped = freshness.get("unmapped_assets") or []
    if unmapped:
        lines.append("Assets still needing a market-data ticker:")
        for a in unmapped:
            lines.append(f"  - {a['symbol']}")

    if pending:
        lines.append(f"{len(pending)} security resolution(s) still waiting on review or an agent.")

    lines.append("Decide whether this is worth a note, then call save_note exactly once.")
    return "\n".join(lines)


class ImportReviewerAgent:
    """Two entry points, both calling the same `_run`:

    - `run()` — sync, for a caller that isn't already inside an event
      loop (same pattern as SecurityResolverAgent.run()).
    - `arun()` — async, for `commit_import` (app/api/routes/imports.py),
      which is itself `async def` (it awaits UploadFile.read()) and so is
      already running on the event loop when it calls this — `run()`'s
      `asyncio.run()` would raise "cannot be called from a running event
      loop" there. This is not a hypothetical: a live commit_import run
      hit exactly that error before `arun()` existed."""

    def run(self, account_id: int) -> AgentRunResult:
        return asyncio.run(self._run(account_id))

    async def arun(self, account_id: int) -> AgentRunResult:
        return await self._run(account_id)

    async def _run(self, account_id: int) -> AgentRunResult:
        try:
            result = await asyncio.wait_for(self._run_loop(account_id), timeout=settings.agent_timeout_seconds)
        except TimeoutError:
            result = AgentRunResult(status="TIMEOUT", steps=0, error="agent run exceeded AGENT_TIMEOUT_SECONDS")
        except Exception as exc:  # noqa: BLE001 — deliberate: the caller (an import request) must
            # never fail because the model or an MCP subprocess misbehaved; record it and fall
            # through to the fallback note below instead — see SecurityResolverAgent's identical reasoning.
            result = AgentRunResult(status="ERROR", steps=0, error=str(exc))

        if result.status not in TERMINAL_TOOLS.values():
            # Deterministic fallback: never leave a broken run silent — write
            # a note saying the review itself failed, so it's visible.
            self._save_fallback_note(account_id, result)
        self._record_run(result)
        return result

    async def _run_loop(self, account_id: int) -> AgentRunResult:
        async with open_mcp_servers(SERVERS) as session:
            mispriced = await session.call_tool("check_import_prices", {"account_id": account_id})
            freshness = await session.call_tool("get_data_freshness", {})
            pending = await session.call_tool("list_pending_resolutions", {})
            return await run_agent(
                system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
                user_message=render_user_message(account_id, mispriced, freshness, pending),
                tools=await session.tool_schemas(),
                call_tool=session.call_tool,
                chat_client=build_chat_client(),
                max_steps=settings.agent_max_steps,
                terminal_tools=TERMINAL_TOOLS,
            )

    def _save_fallback_note(self, account_id: int, result: AgentRunResult) -> None:
        db = SessionLocal()
        try:
            note_repo(db).add(
                agent=AGENT_NAME,
                scope="account",
                title="Import review didn't complete",
                body=f"The automated review ended with status {result.status}"
                + (f": {result.error}" if result.error else "") + ".",
                account_id=account_id,
            )
            db.commit()
        finally:
            db.close()

    def _record_run(self, result: AgentRunResult) -> None:
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
