"""Async helper around the MCP SDK's stdio client, adapted for the agent
loop: tool schemas converted to Ollama's function-calling shape, and
call_tool() returning a plain JSON-safe value (list/dict/None) instead of
the SDK's CallToolResult — every tool under ai/mcp_servers already returns
a small dict/list/None (see ai/common/jsonable.py), so unwrapping the
protocol envelope once here means the agent loop and evaluation replay
(ai/agents/*/evaluation/replay.py) share exactly the same call_tool shape."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _unwrap(structured: dict[str, Any] | None) -> Any:
    """The SDK wraps a tool's plain dict/list/None return in
    `{"result": ...}` inside `structured_content` (it only leaves a result
    unwrapped for a tool whose return type is itself an object schema with
    named properties — none of ours are). Every tool here returns a plain
    value, so this undoes that wrapping unconditionally."""
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
        and prompt never need to distinguish the two."""
        result = await self._session.call_tool(name, arguments)
        if result.is_error:
            text = result.content[0].text if result.content else f"{name} failed"
            return {"error": text}
        return _unwrap(result.structured_content)


@asynccontextmanager
async def open_mcp_server(module: str, tool_names: set[str] | None = None) -> AsyncIterator[McpToolSession]:
    """Start `python -m <module>` (e.g. "ai.mcp_servers.security") as a
    stdio MCP server subprocess, and yield a connected McpToolSession.
    `env=dict(os.environ)` is required — the SDK only forwards a minimal
    environment to the subprocess by default, which would drop
    DATABASE_URL/OPENFIGI_API_KEY/etc (see backend/ai/AGENTS.md rule 5)."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        cwd=str(BACKEND_DIR),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield McpToolSession(session, tool_names)
