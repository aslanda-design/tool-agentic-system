"""Enumerates the live MCP tool catalog for indexing (plans/tool_rag.md
section 3.2) — the one tool-RAG module that genuinely needs MCP-client
knowledge, which is why it lives under ai/, not app/ (backend/ai/AGENTS.md
rule 1: app/ must never import ai/). Everything it produces is handed to
app.container.build_refresh_tool_index_use_case, which owns the actual
embedding/persistence — this module stays thin, per rule 2, the same as
every MCP tool.

`example_queries` and `category` aren't derivable from a tool's live MCP
schema — they're curated metadata this plan adds, kept in version control
as one small JSON file per server under ./examples/. See
backend/ai/AGENTS.md's tool RAG rule: every new MCP tool ships with an
entry here."""

from __future__ import annotations

import json
from pathlib import Path

from ai.common.mcp_client import open_mcp_server
from app.application.refresh_tool_index import RawTool

EXAMPLES_DIR = Path(__file__).with_name("examples")

# One line to add here when a new MCP server ships — same shape as every
# agent's SERVERS/CEILING dict, just enumerated once for indexing instead
# of once per agent.
REGISTERED_SERVERS = [
    "ai.mcp_servers.security",
    "ai.mcp_servers.portfolio",
    "ai.mcp_servers.market_data",
    "ai.mcp_servers.analytics",
    "ai.mcp_servers.notes",
    "ai.mcp_servers.quant",
]


def _load_examples(module: str) -> dict:
    path = EXAMPLES_DIR / f"{module.rsplit('.', 1)[-1]}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


async def enumerate_catalog(servers: list[str] | None = None) -> list[RawTool]:
    """Connects to each server exactly like an agent would (one stdio
    subprocess round trip per server), merges its live schema with the
    matching entry in examples/<server>.json, and raises if an examples
    file references a tool that no longer exists — a stale entry left
    behind by a renamed/removed tool is a bug, not a warning."""
    raw_tools: list[RawTool] = []
    for module in servers or REGISTERED_SERVERS:
        examples_by_tool = _load_examples(module)
        seen_tool_names: set[str] = set()
        async with open_mcp_server(module) as session:
            for schema in await session.tool_schemas():
                fn = schema["function"]
                name = fn["name"]
                seen_tool_names.add(name)
                meta = examples_by_tool.get(name, {})
                raw_tools.append(
                    RawTool(
                        server_module=module,
                        tool_name=name,
                        description=fn["description"],
                        example_queries=meta.get("example_queries", []),
                        category=meta.get("category", module.rsplit(".", 1)[-1]),
                        schema_token_estimate=max(len(json.dumps(fn)) // 4, 20),
                    )
                )
        unknown = set(examples_by_tool) - seen_tool_names
        if unknown:
            raise ValueError(f"{module}: examples file references unknown tool(s) {sorted(unknown)} — stale entry?")
    return raw_tools
