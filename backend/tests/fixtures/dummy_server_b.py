"""A tiny, real (but pure/DB-free) MCP server used only to test
ai/common/mcp_client.py's open_mcp_servers — see test_mcp_client_multi.py.
Not part of the app; never imported outside tests/."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

logging.basicConfig(level=logging.INFO, stream=sys.stderr)

mcp = MCPServer("dummy-b")


@mcp.tool()
def tool_b(x: int) -> int:
    """Returns x * 2."""
    return x * 2


if __name__ == "__main__":
    mcp.run()
