"""A tiny, real (but pure/DB-free) MCP server used only to test
ai/common/mcp_client.py — see test_mcp_client_multi.py. Not part of the
app; never imported outside tests/."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("dummy-a")


@mcp.tool()
def tool_a(x: int) -> int:
    """Returns x + 1."""
    return x + 1


@mcp.tool()
def shared_name() -> str:
    """Returns which dummy server answered — used to prove dispatch
    routes to the right one when two servers both define this tool."""
    return "a"


@mcp.tool()
def bare_dict_tool() -> dict:
    """A bare `-> dict` return annotation, same shape as
    ai/mcp_servers/portfolio/tools/get_portfolio_summary.py and
    ai/mcp_servers/market_data/tools/get_data_freshness.py — the real
    shape that triggered structured_content being None on this SDK
    version despite the tool returning real data (see
    ai.common.mcp_client.McpToolSession.call_tool's text-content
    fallback, and test_call_tool_recovers_a_real_value_for_a_bare_dict_return_type)."""
    return {"a": 1, "b": "two", "nested": {"c": [1, 2, 3]}}


if __name__ == "__main__":
    mcp.run()
