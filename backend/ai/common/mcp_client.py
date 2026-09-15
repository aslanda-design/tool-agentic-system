"""Async helper around the MCP SDK's stdio client, adapted for the agent
loop: tool schemas converted to Ollama's function-calling shape, and
call_tool() returning a plain JSON-safe value (list/dict/None) instead of
the SDK's CallToolResult — every tool under ai/mcp_servers already returns
a small dict/list/None (see ai/common/jsonable.py), so unwrapping the
protocol envelope once here means the agent loop and evaluation replay
(ai/agents/*/evaluation/replay.py) share exactly the same call_tool shape.

Two entry points: `open_mcp_server` for an agent that only ever needs one
server (security_resolver), `open_mcp_servers` for one that spans more
than one bounded context (import_reviewer, portfolio_assistant — see
plans/agentic_asset_mapping_phase7_8.md §3.2/§4). Both yield an object with
the same `.tool_schemas()` / `.call_tool(name, args)` shape, so
`ai.common.agent_loop.run_agent` never needs to know which one it got."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _server_params(module: str) -> StdioServerParameters:
    # env=dict(os.environ) is required — the SDK only forwards a minimal
    # environment to the subprocess by default, which would drop
    # DATABASE_URL/OPENFIGI_API_KEY/etc (see backend/ai/AGENTS.md rule 5).
    return StdioServerParameters(command=sys.executable, args=["-m", module], cwd=str(BACKEND_DIR), env=dict(os.environ))


def _unwrap(structured: dict[str, Any] | None) -> Any:
    """The SDK wraps a tool's plain list/None return in `{"result": ...}`
    inside `structured_content`. It leaves a `dict`-shaped return
    unwrapped — but, at least on the SDK version pinned here, it also
    sometimes leaves `structured_content` `None` entirely for a bare
    `-> dict` return annotation (no `| None`, no named-field schema),
    even though the tool returned real data — see call_tool's text-content
    fallback for what actually handles that case; this function only
    undoes the "result" wrapping when structured_content is present."""
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


class McpToolSession:
    """A connected MCP session plus the subset of its tools this agent is
    allowed to use (`tool_names`; None = all of them)."""

    def __init__(self, session: ClientSession, tool_names: set[str] | None = None) -> None:
        self._session = session
        self._tool_names = tool_names

    async def tool_schemas(self) -> list[dict]:
        """This server's tools (filtered to `tool_names`, if given) as
        OpenAI/Groq function-calling schemas — Ollama's chat() accepts this
        exact same shape, so it's one schema for every ChatClient
        (see ai.common.llm)."""
        tools = (await self._session.list_tools()).tools
        if self._tool_names is not None:
            tools = [t for t in tools if t.name in self._tool_names]
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Call one tool and return its unwrapped result. A protocol-level
        failure (unknown tool, bad arguments, an uncaught exception inside
        the tool) comes back as `{"error": "..."}`, same shape as the
        expected-failure dicts the tools themselves return — the agent loop
        and prompt never need to distinguish the two.

        Real bug found via a live import_reviewer run (not caught by any
        test that mocked the MCP layer, the same class of issue Phase 6's
        `_to_ollama_messages` fix was): for a tool whose return type
        annotation is a bare `dict` (e.g. get_portfolio_summary,
        get_data_freshness — dataclasses converted with to_jsonable, no
        `| None`), this SDK version leaves `structured_content` `None`
        even though the tool returned real data — silently turning every
        such call into `None` for the caller. The tool's JSON is always
        still present as the first text content block regardless, so that
        is the fallback below."""
        result = await self._session.call_tool(name, arguments)
        if result.is_error:
            text = result.content[0].text if result.content else f"{name} failed"
            return {"error": text}
        if result.structured_content is not None:
            return _unwrap(result.structured_content)
        if not result.content:
            return None
        try:
            return json.loads(result.content[0].text)
        except (ValueError, TypeError):
            return result.content[0].text


@asynccontextmanager
async def open_mcp_server(module: str, tool_names: set[str] | None = None) -> AsyncIterator[McpToolSession]:
    """Start `python -m <module>` (e.g. "ai.mcp_servers.security") as a
    stdio MCP server subprocess, and yield a connected McpToolSession. For
    an agent that needs tools from more than one server at once, see
    `open_mcp_servers` below."""
    async with stdio_client(_server_params(module)) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield McpToolSession(session, tool_names)


class MultiServerToolSession:
    """Merges several servers' tool schemas into one list for the model,
    and dispatches call_tool(name, args) to whichever server actually owns
    that tool — see open_mcp_servers, which builds one of these.

    Adding a new server to an agent's capabilities (e.g. a future
    quantitative-signals server) is a one-line addition to the `servers`
    dict passed to open_mcp_servers — this class and the agent loop don't
    change."""

    def __init__(self, exposed: list[McpToolSession], dispatch: dict[str, McpToolSession]) -> None:
        self._exposed = exposed  # one filtered McpToolSession per server, for tool_schemas()
        self._dispatch = dispatch  # tool name -> its owning session (unfiltered), for call_tool()

    async def tool_schemas(self) -> list[dict]:
        merged: list[dict] = []
        for session in self._exposed:
            merged.extend(await session.tool_schemas())
        return merged

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Dispatches by tool name regardless of which server's schema is
        exposed to the model — a pre-fetch call an agent makes itself
        (never advertised to the model as a callable tool, e.g.
        `check_import_prices` for import_reviewer) still works, the same
        way McpToolSession.call_tool never checks its own `tool_names`
        filter (that filter only controls what tool_schemas() advertises)."""
        session = self._dispatch.get(name)
        if session is None:
            return {"error": f"unknown tool {name!r}"}
        return await session.call_tool(name, arguments)


@asynccontextmanager
async def open_mcp_servers(servers: dict[str, set[str] | None]) -> AsyncIterator[MultiServerToolSession]:
    """Open several MCP servers at once as one combined tool session.

    `servers`: {module: tool_names} — same `tool_names` meaning as
    open_mcp_server (None = every tool on that server is exposed to the
    model; an empty set = none of that server's tools are exposed to the
    model, but the agent's own code can still call any of them directly
    via `.call_tool()` for pre-fetched context — see
    ai/agents/import_reviewer/agent.py for that pattern).

    Raises ValueError immediately if two servers expose a tool with the
    same name — tool names must stay globally unique across one agent's
    server set, the same way they already are within a single server."""
    async with AsyncExitStack() as stack:
        exposed: list[McpToolSession] = []
        dispatch: dict[str, McpToolSession] = {}
        for module, tool_names in servers.items():
            read, write = await stack.enter_async_context(stdio_client(_server_params(module)))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            exposed.append(McpToolSession(session, tool_names))
            unfiltered = McpToolSession(session, None)
            for tool in (await session.list_tools()).tools:
                if tool.name in dispatch:
                    raise ValueError(f"tool name collision across servers: {tool.name!r} (in {module!r})")
                dispatch[tool.name] = unfiltered
        yield MultiServerToolSession(exposed, dispatch)
