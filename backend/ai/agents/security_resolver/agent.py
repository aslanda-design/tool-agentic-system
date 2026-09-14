"""The security_resolver agent — a local/hosted LLM that finishes a
resolution the deterministic resolver couldn't decide (NEEDS_AGENT), using
the `security` MCP server's tools over a stdio subprocess. See
plans/agentic_asset_mapping.md Phase 6 and prompt.md for the model's
instructions. Only runs when `settings.agent_enabled` is true — see
app/api/routes/resolutions.py::request_agent_resolution, the only caller.

Which model/provider it talks to (Ollama, Groq, ...) is entirely
ai.common.llm.build_chat_client's concern, driven by settings.agent_* —
nothing here hardcodes a backend."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ai.common.agent_loop import AgentRunResult, run_agent
from ai.common.llm import build_chat_client
from ai.common.mcp_client import open_mcp_server
from app.adapters.persistence.session import SessionLocal
from app.config import settings
from app.container import build_resolve_security_use_case, resolution_repo
from app.domain.errors import DomainError, ResolutionNotFoundError
from app.domain.listings import AgentRunRecord

AGENT_NAME = "security_resolver"
MCP_MODULE = "ai.mcp_servers.security"

# Allowlist: fewer tools = better small-model accuracy (plan §6.4).
# list_pending_resolutions/get_resolution/resolve_isin are deliberately left
# out of what the MODEL can call — the resolution is fetched once up front
# by _run_loop and put in the first user message, so the model never needs
# to look one up itself.
AGENT_TOOLS = {
    "validate_listing",
    "search_listings",
    "lookup_isin",
    "add_candidate",
    "save_security_mapping",
    "flag_for_review",
}
TERMINAL_TOOLS = {"save_security_mapping": "SAVED", "flag_for_review": "FLAGGED"}

PROMPT_PATH = Path(__file__).with_name("prompt.md")


def render_user_message(resolution: dict) -> str:
    """Compact text block describing one resolution and its candidates —
    put in the first user message rather than fetched via a tool call, so a
    small model can't skip reading it (plan §6.4)."""
    ctx = resolution["context"]
    broker_line = (
        f"Broker: {ctx['broker_key'] or 'unknown'} · symbol {ctx['broker_symbol']!r} · "
        f"name {ctx['broker_name']!r} · exchange {ctx['broker_exchange'] or '?'} "
        f"({ctx['broker_mic'] or '?'}) · currency {ctx['currency']}"
    )
    lines = [
        f"Resolution {resolution['id']} for asset {resolution['asset_id']} — ISIN {ctx['isin'] or 'unknown'}",
        broker_line,
    ]
    if resolution["note"]:
        lines.append(f"Note from the rules-based scorer: {resolution['note']}")
    lines.append("Candidates (id · score · symbol · currency · MIC · last close · last trade · features):")
    for candidate in resolution["candidates"]:
        info = candidate["info"] or {}
        features = candidate["features"]
        feature_summary = (
            f"isin{'✓' if features.get('isin_confirmed') else '✗'} "
            f"currency{'✓' if features.get('currency_match') else '✗'} "
            f"exchange{features.get('exchange_match', 0)} "
            f"symbol{'✓' if features.get('symbol_match') else '✗'} "
            f"price{'✓' if features.get('has_recent_price') else '✗'}"
        )
        lines.append(
            f"  [{candidate['id']}] {candidate['score']} · {candidate['symbol']} · "
            f"{info.get('currency') or '?'} · {candidate['mic'] or '?'} · "
            f"{info.get('last_close', '?')} · {info.get('last_trade_date') or '?'} · {feature_summary}"
        )
    if not resolution["candidates"]:
        lines.append("  (none yet — use search_listings/lookup_isin to find one)")
    return "\n".join(lines)


class SecurityResolverAgent:
    """Sync entry point for the scheduler thread / a request handler — see
    app/api/routes/resolutions.py::request_agent_resolution, the only caller."""

    def run(self, resolution_id: int) -> AgentRunResult:
        return asyncio.run(self._run(resolution_id))

    async def _run(self, resolution_id: int) -> AgentRunResult:
        try:
            result = await asyncio.wait_for(self._run_loop(resolution_id), timeout=settings.agent_timeout_seconds)
        except TimeoutError:
            result = AgentRunResult(status="TIMEOUT", steps=0, error="agent run exceeded AGENT_TIMEOUT_SECONDS")
        except Exception as exc:  # noqa: BLE001 — deliberate: the caller (a route/scheduler) must
            # never crash because the model or the MCP subprocess misbehaved; record it and fall
            # through to the flag_for_review fallback below instead.
            result = AgentRunResult(status="ERROR", steps=0, error=str(exc))

        if result.status not in TERMINAL_TOOLS.values():
            # Deterministic fallback: never leave a resolution stuck in
            # NEEDS_AGENT because the model/loop failed — hand it to a human.
            self._flag_after_failure(resolution_id, result)
        self._record_run(resolution_id, result)
        return result

    async def _run_loop(self, resolution_id: int) -> AgentRunResult:
        async with open_mcp_server(MCP_MODULE, AGENT_TOOLS) as session:
            resolution = await session.call_tool("get_resolution", {"resolution_id": resolution_id})
            if resolution is None:
                return AgentRunResult(status="ERROR", steps=0, error=f"resolution {resolution_id} not found")
            return await run_agent(
                system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
                user_message=render_user_message(resolution),
                tools=await session.tool_schemas(),
                call_tool=session.call_tool,
                chat_client=build_chat_client(),
                max_steps=settings.agent_max_steps,
                terminal_tools=TERMINAL_TOOLS,
            )

    def _flag_after_failure(self, resolution_id: int, result: AgentRunResult) -> None:
        db = SessionLocal()
        try:
            use_case = build_resolve_security_use_case(db)
            try:
                use_case.flag_for_review(resolution_id, note=f"agent {result.status}", flagged_by="agent")
            except (ResolutionNotFoundError, DomainError):
                db.rollback()
                return
            db.commit()
        finally:
            db.close()

    def _record_run(self, resolution_id: int, result: AgentRunResult) -> None:
        db = SessionLocal()
        try:
            repo = resolution_repo(db)
            repo.add_agent_run(
                AgentRunRecord(
                    resolution_id=resolution_id,
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
