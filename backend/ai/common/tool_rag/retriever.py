"""Per-turn tool retrieval, called from an agent's `_select_tools` (see
plans/tool_rag.md section 4, and ai/agents/portfolio_assistant/agent.py —
the first caller). Thin by the same rule every MCP tool follows
(backend/ai/AGENTS.md rule 2): no scoring logic here, just resolving what
only ai/ can know (which tools a `None` ceiling entry currently resolves
to, live) and delegating everything else to
app.container.build_retrieve_tools_use_case."""

from __future__ import annotations

import dataclasses

from ai.common.mcp_client import open_mcp_server
from app.adapters.persistence.session import SessionLocal
from app.container import build_retrieve_tools_use_case
from app.domain.tool_rag.types import ToolKey


async def resolve_ceiling_tools(ceiling: dict[str, set[str] | None]) -> set[ToolKey]:
    """A ceiling entry of `None` means "every tool this server currently
    exposes" — only a live MCP session knows what that resolves to today,
    which is why this step happens here rather than inside the
    (ai/-free) app/ use case (plans/tool_rag.md section 2.3)."""
    resolved: set[ToolKey] = set()
    for module, names in ceiling.items():
        if names is not None:
            resolved.update((module, name) for name in names)
            continue
        async with open_mcp_server(module) as session:
            for schema in await session.tool_schemas():
                resolved.add((module, schema["function"]["name"]))
    return resolved


class ToolRetriever:
    """`pinned` — tools that must always survive retrieval regardless of
    score (an agent's `terminal_tools`, if it has any; portfolio_assistant
    has none, since it never ends on a terminal tool — see
    ai/common/agent_loop.py's `terminal_tools=None` conversational mode)."""

    def __init__(self, pinned: set[ToolKey] | None = None) -> None:
        self._pinned = pinned or set()

    async def select(
        self, query: str, ceiling: dict[str, set[str] | None]
    ) -> tuple[dict[str, set[str]], dict | None]:
        """Returns (selection, retrieval_decision) — `selection` is the
        {module: tool_names} shape ai.common.mcp_client.open_mcp_servers
        expects; `retrieval_decision` is a plain JSON-safe dict (or None,
        which never happens here since this method always runs a real
        retrieval — the caller decides whether to call it at all based on
        settings.tool_rag_enabled) for the agent to attach to its
        agent_runs row (plans/tool_rag.md section 5)."""
        all_tools = await resolve_ceiling_tools(ceiling)
        db = SessionLocal()
        try:
            decision = build_retrieve_tools_use_case(db).retrieve(query, self._pinned, all_tools)
        finally:
            db.close()
        selection = {module: set(names) for module, names in decision.selected.items()}
        return selection, dataclasses.asdict(decision)
